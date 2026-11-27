"""
Meilisearch implementation of the adapter contract.

The adapter owns the physical index name, the settings rendered from the
declared schema and the lifecycle tasks. It talks to the HTTP API directly, so
the library keeps Django as its only dependency.

Meilisearch applies index changes asynchronously and answers with a task id.
The lifecycle methods wait for their tasks, because a deploy that ignores a
failed settings update would keep serving the old settings. Writes return the
task id to the caller as a ``WriteReceipt``; ``check_write`` performs one poll
and never sleeps, since scheduling belongs to the indexing worker.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from .adapters import (
    Adapter,
    AdapterError,
    CredentialScope,
    DocumentBatch,
    FilterExpression,
    FilterValue,
    PrimaryKey,
    RebuildTarget,
    ReconcileResult,
    SearchCredential,
    SearchQuery,
    SearchResult,
    WriteFailed,
    WriteReceipt,
    register_adapter,
)
from .config import BackendConfig
from .registry import UseCaseRegistration
from .schema import Searchable
from .transport import HTTPTransport, TransportError

__all__ = [
    "MEILISEARCH_RANKING_RULES",
    "MeilisearchAdapter",
    "register",
    "render_filter",
]

# `sort` first: an explicit sort must outrank relevance, and the rule is inert
# when the query carries no `sort` parameter. The remaining rules are the
# engine defaults, pinned so a deployment's own ranking changes are reverted.
MEILISEARCH_RANKING_RULES = ("sort", "words", "typo", "proximity", "attribute", "exactness")

TASK_TIMEOUT = 30.0
TASK_POLL_INTERVAL = 0.1
MAX_POLL_INTERVAL = 1.0
PAGE_SIZE = 1000


class MeilisearchAdapter(Adapter):
    """The adapter for one Meilisearch deployment."""

    def __init__(
        self,
        registration: UseCaseRegistration,
        backend: BackendConfig,
        *,
        transport: HTTPTransport | None = None,
    ) -> None:
        """Build the transport unless the caller supplies one for testing."""
        super().__init__(registration, backend)
        self._transport = transport or HTTPTransport(
            backend.url,
            {"Authorization": f"Bearer {backend.api_key}"},
        )

    def validate_schema(self) -> None:
        """Meilisearch represents every schema the neutral model allows."""

    def settings(self) -> dict[str, Any]:
        """
        Render the declared schema as Meilisearch index settings.

        The primary key is always filterable so ``id`` filters behave the same
        on both engines, where Typesense filters any declared field.
        """
        searchable = sorted(
            (
                (
                    field.capabilities.searchable.order
                    if isinstance(field.capabilities.searchable, Searchable)
                    else None,
                    position,
                    field.name,
                )
                for position, field in enumerate(self.schema.fields)
                if field.capabilities.searchable
            ),
            key=lambda entry: (entry[0] is None, entry[0] or 0, entry[1]),
        )
        filterable = ["id"] + [
            field.name for field in self.schema.fields if field.capabilities.filterable
        ]
        sortable = [
            field.name for field in self.schema.fields if field.capabilities.sortable
        ]
        return {
            "searchableAttributes": [entry[2] for entry in searchable],
            "filterableAttributes": list(dict.fromkeys(filterable)),
            "sortableAttributes": sortable,
            "rankingRules": list(MEILISEARCH_RANKING_RULES),
        }

    def reconcile(self) -> ReconcileResult:
        """Create the index when missing and apply settings that drifted."""
        changed = False
        live_index = self._index(self.index)
        if live_index is None:
            self._create_index(self.index)
            changed = True
        else:
            self._require_primary_key(self.index, live_index)
        live = self._settings(self.index)
        desired = self.settings()
        differences = {
            name: value for name, value in desired.items() if live.get(name) != value
        }
        if differences:
            self._update_settings(self.index, differences)
            changed = True
        return ReconcileResult(index=self.index, version=self.schema.version, changed=changed)

    def begin_rebuild(self, target: RebuildTarget) -> None:
        """Remove leftover targets and create an empty target index."""
        self._remove_leftovers(target)
        self._create_index(target.name)

    def commit_rebuild(self, target: RebuildTarget) -> None:
        """Swap the target into the live name and delete the old documents."""
        if self._index(self.index) is None:
            self._create_index(self.index)
        self._swap(self.index, target.name)
        self._delete_index(target.name)

    def abort_rebuild(self, target: RebuildTarget) -> None:
        """Delete the target; a target that is already gone is not an error."""
        self._delete_index(target.name, missing_ok=True)

    def upsert(self, documents: DocumentBatch, target: RebuildTarget | None = None) -> WriteReceipt:
        """Submit the batch to the live index or to a rebuild target."""
        uid = self._uid(target)
        payload = [_jsonable(document) for document in documents]
        response = self._transport.request(
            "POST",
            f"/indexes/{_quote(uid)}/documents",
            body=payload,
        )
        return _receipt(response)

    def delete(
        self,
        primary_keys: Sequence[PrimaryKey] | None = None,
        *,
        filter: FilterExpression | None = None,  # pylint: disable=redefined-builtin
        target: RebuildTarget | None = None,
    ) -> WriteReceipt:
        """Delete by primary key or by the rendered filter expression."""
        uid = self._uid(target)
        path = f"/indexes/{_quote(uid)}/documents"
        if primary_keys is not None:
            response = self._transport.request(
                "POST",
                f"{path}/delete-batch",
                body=list(primary_keys),
            )
        elif filter is not None:
            response = self._transport.request(
                "POST",
                f"{path}/delete",
                body={"filter": render_filter(filter)},
            )
        else:
            raise ValueError("delete requires primary_keys or filter")
        return _receipt(response)

    def check_write(self, receipt: WriteReceipt) -> WriteReceipt:
        """Poll the task once; a failed or canceled task raises ``WriteFailed``."""
        if receipt.complete or receipt.task_id is None:
            return receipt
        task = self._transport.request("GET", f"/tasks/{receipt.task_id}")
        return _poll_receipt(receipt, task)

    def search(self, query: SearchQuery) -> SearchResult:
        """
        Run one query against the live index.

        The request carries the text, one page and the rendered filter. The
        page is checked first against the live ``pagination.maxTotalHits`` from
        ``GET /indexes/{uid}/settings``, which costs one extra request per
        search, so a window Meilisearch would clamp is rejected instead of
        returning a short page. ``estimatedTotalHits`` is an estimate, not an
        exact count, so it is never reported as exact.
        """
        max_total_hits = self._max_total_hits(self.index)
        if query.offset + query.limit > max_total_hits:
            raise AdapterError(
                f"Meilisearch index {self.index!r} returns at most {max_total_hits} hits; "
                f"offset {query.offset} plus limit {query.limit} exceeds it"
            )
        payload: dict[str, Any] = {
            "offset": query.offset,
            "limit": query.limit,
        }
        if query.text:
            payload["q"] = query.text
        if query.filters:
            payload["filter"] = render_filter(query.filters)
        response = self._transport.request(
            "POST",
            f"/indexes/{_quote(self.index)}/search",
            body=payload,
        )
        if not isinstance(response, Mapping):
            raise AdapterError("Meilisearch returned an invalid search response")
        total = response.get("estimatedTotalHits")
        if not _is_nonnegative_int(total):
            raise AdapterError("Meilisearch returned no estimated total")
        hits = response.get("hits")
        if not isinstance(hits, list):
            raise AdapterError("Meilisearch returned invalid search hits")
        for hit in hits:
            if not isinstance(hit, Mapping):
                raise AdapterError("Meilisearch returned an invalid search hit")
        return SearchResult(
            documents=tuple(dict(hit) for hit in hits),
            total=total,
            total_is_exact=False,
        )

    def mint_credential(
        self,
        scope: CredentialScope,
        expires_in: timedelta,
    ) -> SearchCredential:
        """
        Mint a tenant token for the live index.

        ADR 0002: the parent key is ``search_api_key`` when set, otherwise the
        write ``api_key``; either must be listed by the engine and allow
        ``search``. The master key is not listed and cannot sign a token. The
        token embeds the scope's filters for the live index and expires after
        ``expires_in``.
        """
        parent_key = self.backend.search_api_key or self.backend.api_key
        key_uid = self._tenant_key(parent_key)
        search_rules: dict[str, Any] = {self.index: {}}
        if scope.filters:
            search_rules[self.index]["filter"] = render_filter(scope.filters)
        token = _tenant_token(key_uid, parent_key, search_rules, _now() + expires_in)
        return SearchCredential(
            url=self.backend.public_url or self.backend.url,
            index_name=self.index,
            api_key=token,
        )

    def _uid(self, target: RebuildTarget | None) -> str:
        """Return the index a write goes to."""
        return target.name if target is not None else self.index

    def _index(self, uid: str) -> Mapping[str, Any] | None:
        """Return the index description, or ``None`` when it does not exist."""
        try:
            index = self._transport.request("GET", f"/indexes/{_quote(uid)}")
        except TransportError as error:
            if error.not_found:
                return None
            raise
        if not isinstance(index, Mapping):
            raise AdapterError("Meilisearch returned an invalid index description")
        return index

    def _require_primary_key(self, uid: str, index: Mapping[str, Any]) -> None:
        """Reject an existing index whose primary key is not the declared ``id``."""
        primary_key = index.get("primaryKey")
        if primary_key is not None and primary_key != "id":
            raise AdapterError(
                f"index {uid!r} uses primary key {primary_key!r}, expected 'id'"
            )

    def _create_index(self, uid: str) -> None:
        """Create the index with the primary key and apply the settings."""
        response = self._transport.request(
            "POST",
            "/indexes",
            body={"uid": uid, "primaryKey": "id"},
        )
        self._wait(response)
        self._update_settings(uid, self.settings())

    def _settings(self, uid: str) -> Mapping[str, Any]:
        """Return the live settings of an index."""
        settings = self._transport.request("GET", f"/indexes/{_quote(uid)}/settings")
        if not isinstance(settings, Mapping):
            raise AdapterError("Meilisearch returned invalid index settings")
        return settings

    def _max_total_hits(self, uid: str) -> int:
        """Return the live index's page cap, ``pagination.maxTotalHits``."""
        pagination = self._settings(uid).get("pagination")
        max_total_hits = (
            pagination.get("maxTotalHits") if isinstance(pagination, Mapping) else None
        )
        if not _is_nonnegative_int(max_total_hits):
            raise AdapterError("Meilisearch returned no pagination.maxTotalHits")
        return max_total_hits

    def _update_settings(self, uid: str, settings: Mapping[str, Any]) -> None:
        """Patch the given settings and wait for the task."""
        response = self._transport.request(
            "PATCH",
            f"/indexes/{_quote(uid)}/settings",
            body=dict(settings),
        )
        self._wait(response)

    def _swap(self, first: str, second: str) -> None:
        """Swap two indexes and wait for the task."""
        response = self._transport.request(
            "POST",
            "/swap-indexes",
            body=[{"indexes": [first, second]}],
        )
        self._wait(response)

    def _delete_index(self, uid: str, *, missing_ok: bool = False) -> None:
        """Delete an index and wait for the task."""
        try:
            response = self._transport.request("DELETE", f"/indexes/{_quote(uid)}")
        except TransportError as error:
            if missing_ok and error.not_found:
                return
            raise
        self._wait(response)

    def _remove_leftovers(self, target: RebuildTarget) -> None:
        """Delete targets of this index and version other than ``target``."""
        prefix = f"{target.index}_v{target.version}_"
        for name in self._index_names():
            if name.startswith(prefix) and name != target.name:
                self._delete_index(name)

    def _index_names(self) -> list[str]:
        """List every index uid, one page at a time."""
        names: list[str] = []
        offset = 0
        while True:
            page = self._transport.request(
                "GET",
                f"/indexes?offset={offset}&limit={PAGE_SIZE}",
            )
            results = page.get("results") if isinstance(page, Mapping) else None
            if not isinstance(results, list):
                raise AdapterError("Meilisearch returned an invalid index list")
            names.extend(
                entry["uid"]
                for entry in results
                if isinstance(entry, Mapping) and isinstance(entry.get("uid"), str)
            )
            if len(results) < PAGE_SIZE:
                return names
            offset += len(results)

    def _tenant_key(self, key: str) -> str:
        """Return the uid of the key that signs tenant tokens."""
        page = self._transport.request("GET", "/keys?limit=1000")
        results = page.get("results") if isinstance(page, Mapping) else None
        if not isinstance(results, list):
            raise AdapterError("Meilisearch returned an invalid key list")
        for entry in results:
            if not isinstance(entry, Mapping) or entry.get("key") != key:
                continue
            uid = entry.get("uid")
            if not isinstance(uid, str) or not uid:
                raise AdapterError("Meilisearch returned an invalid key uid")
            actions = entry.get("actions")
            if not isinstance(actions, list) or ("*" not in actions and "search" not in actions):
                raise AdapterError("the tenant token key must allow search")
            return uid
        raise AdapterError(
            "Meilisearch did not list the tenant token key; use a listed API key "
            "or configure search_api_key"
        )

    def _wait(self, response: object) -> None:
        """Wait for a lifecycle task to succeed."""
        task_id = _task_id(response)
        deadline = time.monotonic() + TASK_TIMEOUT
        interval = TASK_POLL_INTERVAL
        while True:
            task = self._transport.request("GET", f"/tasks/{task_id}")
            status = task.get("status") if isinstance(task, Mapping) else None
            if status == "succeeded":
                return
            if status in ("failed", "canceled"):
                raise AdapterError(
                    f"Meilisearch task {task_id} {status} ({_task_error(task)})"
                )
            if time.monotonic() > deadline:
                raise AdapterError(
                    f"Meilisearch task {task_id} did not finish within {TASK_TIMEOUT:g}s"
                )
            time.sleep(interval)
            interval = min(interval * 2, MAX_POLL_INTERVAL)


def render_filter(expression: FilterExpression) -> str:
    """Render the abstract filter expression in Meilisearch syntax."""
    groups = []
    for group in expression:
        terms = [
            f"{term.field} IN [{', '.join(_literal(value) for value in term.values)}]"
            for term in group.terms
        ]
        groups.append("(" + " OR ".join(terms) + ")")
    return " AND ".join(groups)


def register() -> None:
    """Make the Meilisearch adapter the factory for its engine."""
    register_adapter("meilisearch", MeilisearchAdapter, replace=True)


def _quote(uid: str) -> str:
    """Quote an index uid for a URL path."""
    return quote(uid, safe="")


def _receipt(response: object) -> WriteReceipt:
    """Build a receipt from a write acknowledgment."""
    task_id = _task_id(response)
    return WriteReceipt(task_id=task_id)


def _task_id(response: object) -> int:
    """Return the task id of an asynchronous acknowledgment."""
    task_id = response.get("taskUid") if isinstance(response, Mapping) else None
    if isinstance(task_id, bool) or not isinstance(task_id, int):
        raise AdapterError("Meilisearch did not acknowledge the request with a task")
    return task_id


def _is_nonnegative_int(value: object) -> bool:
    """Return whether the engine sent a count: an int, not a bool, at least zero."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _task_error(task: object) -> str:
    """Describe a failed task without including the response body."""
    error = task.get("error") if isinstance(task, Mapping) else None
    code = error.get("code") if isinstance(error, Mapping) else None
    return code if isinstance(code, str) and code else "unknown error"


def _poll_receipt(receipt: WriteReceipt, task: object) -> WriteReceipt:
    """Map one task status to a receipt, raising when the write failed."""
    status = task.get("status") if isinstance(task, Mapping) else None
    if status == "succeeded":
        return WriteReceipt(task_id=receipt.task_id, complete=True)
    if status in ("failed", "canceled"):
        raise WriteFailed(
            f"Meilisearch task {receipt.task_id} {status} ({_task_error(task)})"
        )
    return WriteReceipt(task_id=receipt.task_id, complete=False)


def _literal(value: FilterValue) -> str:
    """Render one filter value in Meilisearch syntax."""
    if isinstance(value, datetime):
        return str(int(value.timestamp()))
    return json.dumps(value)


def _jsonable(value: Any) -> Any:
    """Convert datetimes to POSIX seconds so the document is JSON-safe."""
    if isinstance(value, datetime):
        return int(value.timestamp())
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(timezone.utc)


def _tenant_token(
    api_key_uid: str,
    api_key: str,
    search_rules: Mapping[str, Any],
    expires_at: datetime,
) -> str:
    """Sign a Meilisearch tenant token with HMAC-SHA256."""
    header = {"typ": "JWT", "alg": "HS256"}
    payload = {
        "apiKeyUid": api_key_uid,
        "searchRules": search_rules,
        "exp": int(expires_at.timestamp()),
    }
    header_part = _base64url(json.dumps(header, separators=(",", ":")).encode())
    payload_part = _base64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_part}.{payload_part}".encode()
    signature = hmac.new(api_key.encode(), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_base64url(signature)}"


def _base64url(data: bytes) -> str:
    """Encode bytes as unpadded base64url."""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
