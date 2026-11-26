"""Tests for the Meilisearch adapter."""

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from django.test import override_settings

from openedx_search.adapters import (
    AdapterError,
    CredentialScope,
    FilterGroup,
    FilterTerm,
    RebuildTarget,
    SearchCredential,
    SearchQuery,
    SearchResult,
    WriteFailed,
    WriteReceipt,
    get_adapter,
)
from openedx_search.config import BackendConfig
from openedx_search.meilisearch import MEILISEARCH_RANKING_RULES, PAGE_SIZE, MeilisearchAdapter, register, render_filter
from openedx_search.registry import UseCaseRegistration
from openedx_search.schema import Capabilities, Field, FieldType, IndexSchema, Searchable
from openedx_search.transport import TransportError
from tests.fixtures import STUDIO_COURSE

MOMENT = datetime(2026, 1, 1, tzinfo=timezone.utc)
MOMENT_SECONDS = 1767225600
TENANT_KEY = "test-key"
TENANT_UID = "00000000-0000-4000-8000-000000000000"
TENANT_TOKEN = (
    "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9"
    ".eyJhcGlLZXlVaWQiOiIwMDAwMDAwMC0wMDAwLTQwMDAtODAwMC0wMDAwMDAwMDAwMDAiLCJzZWFyY2hSdWxlcyI6"
    "eyJzdHVkaW9fY29udGVudCI6eyJmaWx0ZXIiOiIob3JnIElOIFtcIm9wZW5lZHhcIl0pIn19LCJleHAiOjE3OTg3NjE2MDB9"
    ".tlqBwpZqVc4MbxgZuulCkLPOyJO_OdTQLahR7Swqy8g"
)


class FakeTransport:
    """Records requests and replays queued responses."""

    def __init__(self, responses=None, *, tasks=None):
        """Start with the given responses, in order, and task statuses."""
        self.responses = list(responses or [])
        self.tasks = dict(tasks or {})
        self.task_statuses = {}
        self.task_errors = {}
        self.requests = []

    def request(self, method, path, body=None):
        """Record the request and return the next response or task status."""
        self.requests.append((method, path, body))
        if method == "GET" and path.startswith("/tasks/"):
            task_id = int(path.rsplit("/", 1)[1])
            statuses = self.task_statuses.get(task_id)
            status = statuses.pop(0) if statuses else self.tasks.get(task_id, "succeeded")
            return {
                "taskUid": task_id,
                "status": status,
                "error": self.task_errors.get(task_id),
            }
        if not self.responses:
            raise AssertionError(f"unexpected request {method} {path}")  # pragma: no cover
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def fail(self, task_id, code):
        """Make the given task report a failure."""
        self.tasks[task_id] = "failed"
        self.task_errors[task_id] = {"code": code}

    def paths(self):
        """Return the method and path of every request, in order."""
        return [(method, path) for method, path, _ in self.requests]


def _adapter(registration=STUDIO_COURSE, transport=None):
    """Build an adapter that talks to the given fake transport."""
    backend = BackendConfig(
        name="default",
        engine="meilisearch",
        url="http://engine:7700",
        api_key="secret",
    )
    return MeilisearchAdapter(registration, backend, transport=transport or FakeTransport())


def _decode_token(token):
    """Decode a tenant token payload without verifying it."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def _verify_token(token, key):
    """Verify a tenant token signature with its parent key."""
    header, payload, signature = token.split(".")
    expected = base64.urlsafe_b64encode(
        hmac.new(key.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).decode("ascii").rstrip("=")
    assert signature == expected


def _key_page(key=TENANT_KEY, *, uid=TENANT_UID, actions=("search",)):
    """Build a /keys page with one key."""
    return {
        "results": [{"uid": uid, "key": key, "actions": list(actions), "indexes": ["*"]}],
        "offset": 0,
        "limit": 1000,
        "total": 1,
    }


def _tenant_adapter(transport, *, api_key=TENANT_KEY, search_api_key=None):
    """Build an adapter whose backend can mint tenant tokens."""
    backend = BackendConfig(
        name="default",
        engine="meilisearch",
        url="http://engine:7700",
        public_url="https://search.example.com",
        api_key=api_key,
        search_api_key=search_api_key,
    )
    return MeilisearchAdapter(STUDIO_COURSE, backend, transport=transport)


def test_settings_render_the_schema():
    """Searchable order, filterable paths, sortable fields and ranking rules."""
    settings = _adapter().settings()

    assert settings["searchableAttributes"] == [
        "display_name",
        "block_id",
        "content",
        "description",
        "tags",
        "tags.taxonomy",
        "tags.level0",
        "tags.level1",
        "tags.level2",
        "tags.level3",
    ]
    assert settings["filterableAttributes"] == [
        "id",
        "usage_key",
        "block_id",
        "block_type",
        "context_key",
        "org",
        "access_id",
        "breadcrumbs.usage_key",
        "tags",
        "tags.taxonomy",
        "tags.level0",
        "tags.level1",
        "tags.level2",
        "tags.level3",
        "content.problem_types",
    ]
    assert settings["sortableAttributes"] == ["display_name", "modified"]
    assert settings["rankingRules"] == list(MEILISEARCH_RANKING_RULES)
    assert settings["rankingRules"][0] == "sort"


def test_searchable_fields_without_an_order_come_last():
    """Only declared orders rank; the rest keep declaration order."""
    schema = IndexSchema(
        name="order",
        fields=(
            Field("id", FieldType.STRING, required=True),
            Field("unsorted", capabilities=Capabilities(searchable=True)),
            Field("ranked", capabilities=Capabilities(searchable=Searchable(order=1))),
        ),
    )
    registration = UseCaseRegistration(use_case="order", index="order_index", schema=schema)

    settings = _adapter(registration).settings()

    assert settings["searchableAttributes"] == ["ranked", "unsorted"]
    assert settings["filterableAttributes"] == ["id"]


def test_reconcile_creates_and_configures_a_missing_index():
    """A missing index is created with the primary key and the settings."""
    live = _adapter().settings()
    transport = FakeTransport(
        [TransportError("missing", status=404), {"taskUid": 1}, {"taskUid": 2}, live]
    )
    adapter = _adapter(transport=transport)

    result = adapter.reconcile()

    assert result.changed
    assert transport.paths() == [
        ("GET", "/indexes/studio_content"),
        ("POST", "/indexes"),
        ("GET", "/tasks/1"),
        ("PATCH", "/indexes/studio_content/settings"),
        ("GET", "/tasks/2"),
        ("GET", "/indexes/studio_content/settings"),
    ]
    assert transport.requests[1][2] == {"uid": "studio_content", "primaryKey": "id"}
    assert transport.requests[3][2] == live


def test_reconcile_reports_no_change_for_a_matching_index():
    """An index with the declared settings is left alone."""
    live = _adapter().settings()
    transport = FakeTransport([{}, live])

    result = _adapter(transport=transport).reconcile()

    assert not result.changed
    assert transport.paths() == [
        ("GET", "/indexes/studio_content"),
        ("GET", "/indexes/studio_content/settings"),
    ]


def test_reconcile_rejects_a_foreign_primary_key():
    """Writes address documents by ``id``; another primary key would break them."""
    transport = FakeTransport([{"uid": "studio_content", "primaryKey": "key"}])

    with pytest.raises(AdapterError, match="primary key"):
        _adapter(transport=transport).reconcile()


def test_reconcile_patches_only_the_settings_that_drifted():
    """A changed ranking order is the only thing sent back."""
    live = _adapter().settings()
    drifted = {**live, "rankingRules": ["words"]}
    transport = FakeTransport([{}, drifted, {"taskUid": 3}])

    result = _adapter(transport=transport).reconcile()

    assert result.changed
    assert transport.requests[2] == (
        "PATCH",
        "/indexes/studio_content/settings",
        {"rankingRules": list(MEILISEARCH_RANKING_RULES)},
    )


def test_reconcile_fails_when_the_engine_rejects_the_task():
    """A failed lifecycle task is an adapter error, not a silent success."""
    transport = FakeTransport([TransportError("missing", status=404), {"taskUid": 1}])
    transport.fail(1, "index_creation_failed")

    with pytest.raises(AdapterError, match="index_creation_failed"):
        _adapter(transport=transport).reconcile()


def test_upsert_returns_the_task_receipt():
    """A batch reaches the live index and its task becomes the receipt."""
    transport = FakeTransport([{"taskUid": 42}])

    receipt = _adapter(transport=transport).upsert([{"id": "doc", "usage_key": "x"}])

    assert receipt == WriteReceipt(task_id=42)
    assert transport.requests == [
        ("POST", "/indexes/studio_content/documents", [{"id": "doc", "usage_key": "x"}]),
    ]


def test_upsert_renders_datetimes_as_posix_seconds():
    """The engine stores seconds, and the document validator accepts a datetime."""
    transport = FakeTransport([{"taskUid": 1}])

    _adapter(transport=transport).upsert([{"id": "doc", "modified": MOMENT}])

    assert transport.requests[0][2] == [{"id": "doc", "modified": MOMENT_SECONDS}]


def test_upsert_can_target_a_rebuild():
    """A rebuild batch goes to the target's physical index, not the live one."""
    target = RebuildTarget(index="studio_content", version=1, generation="gen1")
    transport = FakeTransport([{"taskUid": 1}])

    _adapter(transport=transport).upsert([{"id": "doc"}], target=target)

    assert target.name == "studio_content_v1_gen1"
    assert transport.requests[0][1] == "/indexes/studio_content_v1_gen1/documents"


def test_delete_by_primary_keys_uses_the_batch_endpoint():
    """A list of ids is one bounded delete request."""
    transport = FakeTransport([{"taskUid": 5}])

    receipt = _adapter(transport=transport).delete(("doc-1", "doc-2"))

    assert receipt == WriteReceipt(task_id=5)
    assert transport.requests == [
        ("POST", "/indexes/studio_content/documents/delete-batch", ["doc-1", "doc-2"]),
    ]


def test_delete_by_filter_renders_the_expression():
    """The abstract expression becomes Meilisearch filter syntax."""
    expression = (
        FilterGroup(
            (
                FilterTerm("org", ("openedx",)),
                FilterTerm("access_id", (1,)),
            )
        ),
    )
    transport = FakeTransport([{"taskUid": 6}])

    _adapter(transport=transport).delete(filter=expression)

    assert transport.requests == [
        (
            "POST",
            "/indexes/studio_content/documents/delete",
            {"filter": '(org IN ["openedx"] OR access_id IN [1])'},
        ),
    ]


def test_delete_requires_a_form():
    """The adapter refuses a delete with neither keys nor a filter."""
    with pytest.raises(ValueError, match="primary_keys or filter"):
        _adapter().delete()


def test_render_filter_keeps_group_and_term_precedence():
    """Groups are ANDed; terms inside a group are ORed, with typed literals."""
    expression = (
        FilterGroup((FilterTerm("org", ("a", "b")),)),
        FilterGroup(
            (
                FilterTerm("published", (True,)),
                FilterTerm("modified", (MOMENT,)),
            )
        ),
    )

    assert render_filter(expression) == (
        '(org IN ["a", "b"]) AND (published IN [true] OR modified IN [1767225600])'
    )


def test_check_write_reports_an_unfinished_task():
    """One poll returns an incomplete receipt while the task is running."""
    transport = FakeTransport(tasks={7: "processing"})

    receipt = _adapter(transport=transport).check_write(WriteReceipt(task_id=7))

    assert receipt == WriteReceipt(task_id=7, complete=False)
    assert transport.paths() == [("GET", "/tasks/7")]


def test_check_write_completes_a_succeeded_task():
    """A succeeded task marks the receipt complete."""
    transport = FakeTransport()

    receipt = _adapter(transport=transport).check_write(WriteReceipt(task_id=7))

    assert receipt == WriteReceipt(task_id=7, complete=True)


def test_check_write_raises_for_a_failed_task():
    """A failed task is a write failure, not a receipt to poll again."""
    transport = FakeTransport()
    transport.fail(7, "invalid_document")

    with pytest.raises(WriteFailed, match="invalid_document"):
        _adapter(transport=transport).check_write(WriteReceipt(task_id=7))


def test_check_write_skips_a_complete_receipt():
    """An already complete receipt costs no request."""
    transport = FakeTransport()

    result = _adapter(transport=transport).check_write(WriteReceipt(task_id=7, complete=True))

    assert result == WriteReceipt(task_id=7, complete=True)
    assert not transport.requests


def test_begin_rebuild_removes_leftovers_and_creates_the_target():
    """Only targets of this index and version are removed; the target is new."""
    target = RebuildTarget(index="studio_content", version=1, generation="gen1")
    settings = _adapter().settings()
    transport = FakeTransport(
        [
            {
                "results": [
                    {"uid": "studio_content_v1_old"},
                    {"uid": "studio_content_v1_gen1"},
                    {"uid": "other"},
                ],
                "offset": 0,
                "limit": 1000,
                "total": 3,
            },
            {"taskUid": 1},
            {"taskUid": 2},
            {"taskUid": 3},
        ]
    )

    _adapter(transport=transport).begin_rebuild(target)

    assert transport.paths() == [
        ("GET", "/indexes?offset=0&limit=1000"),
        ("DELETE", "/indexes/studio_content_v1_old"),
        ("GET", "/tasks/1"),
        ("POST", "/indexes"),
        ("GET", "/tasks/2"),
        ("PATCH", "/indexes/studio_content_v1_gen1/settings"),
        ("GET", "/tasks/3"),
    ]
    assert transport.requests[3][2] == {
        "uid": "studio_content_v1_gen1",
        "primaryKey": "id",
    }
    assert transport.requests[5][2] == settings


def test_commit_rebuild_swaps_the_target_and_deletes_it():
    """The live name keeps the documents; the old copy is discarded."""
    target = RebuildTarget(index="studio_content", version=1, generation="gen1")
    transport = FakeTransport([{}, {"taskUid": 1}, {"taskUid": 2}])

    _adapter(transport=transport).commit_rebuild(target)

    assert transport.paths() == [
        ("GET", "/indexes/studio_content"),
        ("POST", "/swap-indexes"),
        ("GET", "/tasks/1"),
        ("DELETE", "/indexes/studio_content_v1_gen1"),
        ("GET", "/tasks/2"),
    ]
    assert transport.requests[1][2] == [
        {"indexes": ["studio_content", "studio_content_v1_gen1"]},
    ]


def test_commit_rebuild_creates_the_live_index_when_missing():
    """The first deploy has no live index to swap with, so it is created."""
    target = RebuildTarget(index="studio_content", version=1, generation="gen1")
    transport = FakeTransport(
        [
            TransportError("missing", status=404),
            {"taskUid": 1},
            {"taskUid": 2},
            {"taskUid": 3},
            {"taskUid": 4},
        ]
    )

    _adapter(transport=transport).commit_rebuild(target)

    assert transport.paths()[:4] == [
        ("GET", "/indexes/studio_content"),
        ("POST", "/indexes"),
        ("GET", "/tasks/1"),
        ("PATCH", "/indexes/studio_content/settings"),
    ]
    assert ("POST", "/swap-indexes") in transport.paths()


def test_abort_rebuild_ignores_a_missing_target():
    """A crash before the target existed leaves nothing to delete."""
    target = RebuildTarget(index="studio_content", version=1, generation="gen1")
    transport = FakeTransport([TransportError("missing", status=404)])

    _adapter(transport=transport).abort_rebuild(target)

    assert transport.paths() == [("DELETE", "/indexes/studio_content_v1_gen1")]


def test_abort_rebuild_deletes_the_target():
    """A target that exists is deleted and its task awaited."""
    target = RebuildTarget(index="studio_content", version=1, generation="gen1")
    transport = FakeTransport([{"taskUid": 9}])

    _adapter(transport=transport).abort_rebuild(target)

    assert transport.paths() == [
        ("DELETE", "/indexes/studio_content_v1_gen1"),
        ("GET", "/tasks/9"),
    ]


def test_search_sends_the_text_page_and_filters():
    """The text, the page and the rendered filter reach the engine in one search."""
    transport = FakeTransport(
        [
            {"pagination": {"maxTotalHits": 1000}},
            {"hits": [{"id": "doc-1"}], "estimatedTotalHits": 2},
        ]
    )
    query = SearchQuery(
        text="display name",
        filters=(
            FilterGroup((FilterTerm("id", ("doc-1", "doc-2")),)),
            FilterGroup((FilterTerm("block_type", ("video",)),)),
        ),
        offset=20,
        limit=10,
    )

    result = _adapter(transport=transport).search(query)

    assert transport.requests == [
        ("GET", "/indexes/studio_content/settings", None),
        (
            "POST",
            "/indexes/studio_content/search",
            {
                "offset": 20,
                "limit": 10,
                "q": "display name",
                "filter": '(id IN ["doc-1", "doc-2"]) AND (block_type IN ["video"])',
            },
        ),
    ]
    assert result == SearchResult(
        documents=({"id": "doc-1"},),
        total=2,
        total_is_exact=False,
    )


def test_search_defaults_to_the_first_page():
    """An empty query sends the default window and neither q nor filter."""
    transport = FakeTransport(
        [
            {"pagination": {"maxTotalHits": 1000}},
            {"hits": [], "estimatedTotalHits": 0},
        ]
    )

    result = _adapter(transport=transport).search(SearchQuery())

    assert transport.requests[1] == (
        "POST",
        "/indexes/studio_content/search",
        {"offset": 0, "limit": 20},
    )
    assert result == SearchResult(documents=(), total=0)


def test_search_rejects_a_page_beyond_the_live_page_cap():
    """The live maxTotalHits is enforced before the search request."""
    transport = FakeTransport(
        [
            {"pagination": {"maxTotalHits": 100}},
            {"hits": [{"id": "doc-1"}], "estimatedTotalHits": 100},
            {"pagination": {"maxTotalHits": 100}},
        ]
    )
    adapter = _adapter(transport=transport)

    adapter.search(SearchQuery(offset=90, limit=10))

    with pytest.raises(AdapterError, match="at most 100"):
        adapter.search(SearchQuery(offset=90, limit=11))

    assert transport.paths() == [
        ("GET", "/indexes/studio_content/settings"),
        ("POST", "/indexes/studio_content/search"),
        ("GET", "/indexes/studio_content/settings"),
    ]


def test_search_requires_the_live_page_cap():
    """Without maxTotalHits the page cannot be checked, so the search fails."""
    with pytest.raises(AdapterError, match="maxTotalHits"):
        _adapter(transport=FakeTransport([{"pagination": {}}])).search(SearchQuery())


@pytest.mark.parametrize(
    "response",
    [
        [],
        {},
        {"hits": [], "estimatedTotalHits": "7"},
        {"hits": [], "estimatedTotalHits": True},
        {"hits": [], "estimatedTotalHits": -1},
        {"hits": "none", "estimatedTotalHits": 7},
        {"hits": ["not a document"], "estimatedTotalHits": 7},
    ],
)
def test_search_rejects_an_invalid_response(response):
    """A malformed body fails instead of returning a wrong result."""
    transport = FakeTransport([{"pagination": {"maxTotalHits": 1000}}, response])

    with pytest.raises(AdapterError):
        _adapter(transport=transport).search(SearchQuery())


def test_mint_credential_matches_the_meilisearch_reference(monkeypatch):
    """The token is byte-identical to the official client's output."""
    monkeypatch.setattr(
        "openedx_search.meilisearch._now",
        lambda: datetime(2026, 12, 31, tzinfo=timezone.utc),
    )
    transport = FakeTransport([_key_page()])
    scope = CredentialScope(filters=(FilterGroup((FilterTerm("org", ("openedx",)),)),))

    credential = _tenant_adapter(transport).mint_credential(scope, timedelta(days=1))

    assert credential == SearchCredential(
        url="https://search.example.com",
        index_name="studio_content",
        api_key=TENANT_TOKEN,
    )
    assert transport.paths() == [("GET", "/keys?limit=1000")]


def test_mint_credential_without_filters_allows_the_index(monkeypatch):
    """An empty scope searches the whole index but no other index."""
    monkeypatch.setattr(
        "openedx_search.meilisearch._now",
        lambda: datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    transport = FakeTransport([_key_page()])

    credential = _tenant_adapter(transport).mint_credential(CredentialScope(), timedelta(hours=1))

    assert _decode_token(credential.api_key)["searchRules"] == {"studio_content": {}}


def test_mint_credential_signs_with_the_search_key(monkeypatch):
    """search_api_key is the parent when configured and signs the token."""
    monkeypatch.setattr(
        "openedx_search.meilisearch._now",
        lambda: datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    transport = FakeTransport([_key_page(key="search-key")])
    adapter = _tenant_adapter(transport, api_key="write-key", search_api_key="search-key")

    credential = adapter.mint_credential(CredentialScope(), timedelta(hours=1))

    _verify_token(credential.api_key, "search-key")
    assert _decode_token(credential.api_key)["apiKeyUid"] == TENANT_UID


def test_mint_credential_requires_a_listed_key():
    """The master key is not listed, so it cannot sign a tenant token."""
    transport = FakeTransport([{"results": [], "offset": 0, "limit": 1000, "total": 0}])

    with pytest.raises(AdapterError, match="did not list"):
        _tenant_adapter(transport).mint_credential(CredentialScope(), timedelta(hours=1))


def test_mint_credential_skips_other_keys():
    """Only the configured key is used, even when it is not the first entry."""
    transport = FakeTransport(
        [
            {
                "results": [
                    {"uid": "other", "key": "other-key", "actions": ["search"], "indexes": ["*"]},
                    {"uid": TENANT_UID, "key": TENANT_KEY, "actions": ["search"], "indexes": ["*"]},
                ],
                "offset": 0,
                "limit": 1000,
                "total": 2,
            }
        ]
    )

    credential = _tenant_adapter(transport).mint_credential(CredentialScope(), timedelta(hours=1))

    assert _decode_token(credential.api_key)["apiKeyUid"] == TENANT_UID


def test_mint_credential_expires_after_the_requested_lifetime():
    """The token expiry is the current time plus the requested lifetime."""
    transport = FakeTransport([_key_page()])
    before = int(datetime.now(timezone.utc).timestamp())

    credential = _tenant_adapter(transport).mint_credential(CredentialScope(), timedelta(hours=1))

    after = int(datetime.now(timezone.utc).timestamp())
    assert before + 3600 <= _decode_token(credential.api_key)["exp"] <= after + 3600


def test_mint_credential_requires_a_search_key():
    """A key without the search action cannot mint a tenant token."""
    transport = FakeTransport([_key_page(actions=("documents.add",))])

    with pytest.raises(AdapterError, match="allow search"):
        _tenant_adapter(transport).mint_credential(CredentialScope(), timedelta(hours=1))


def test_mint_credential_rejects_an_invalid_key_list():
    """The key list must be an object with a results list."""
    transport = FakeTransport([{}])

    with pytest.raises(AdapterError, match="invalid key list"):
        _tenant_adapter(transport).mint_credential(CredentialScope(), timedelta(hours=1))


def test_mint_credential_requires_a_valid_key_uid():
    """An entry without a uid cannot be used as the issuer."""
    transport = FakeTransport([_key_page(uid="")])

    with pytest.raises(AdapterError, match="invalid key uid"):
        _tenant_adapter(transport).mint_credential(CredentialScope(), timedelta(hours=1))


def test_reconcile_propagates_a_transport_failure():
    """A non-404 failure is not treated as a missing index."""
    transport = FakeTransport([TransportError("unavailable", status=503)])

    with pytest.raises(TransportError):
        _adapter(transport=transport).reconcile()


def test_reconcile_rejects_an_invalid_index_response():
    """The index endpoint must return an object."""
    transport = FakeTransport(["not a mapping"])

    with pytest.raises(AdapterError, match="invalid index description"):
        _adapter(transport=transport).reconcile()


def test_reconcile_rejects_invalid_settings():
    """The settings endpoint must return an object."""
    transport = FakeTransport([{}, []])

    with pytest.raises(AdapterError, match="invalid index settings"):
        _adapter(transport=transport).reconcile()


def test_reconcile_waits_for_a_processing_task(monkeypatch):
    """A task that is still running is polled again after a backoff."""
    sleeps = []
    monkeypatch.setattr("openedx_search.meilisearch.time.sleep", sleeps.append)
    live = _adapter().settings()
    transport = FakeTransport(
        [TransportError("missing", status=404), {"taskUid": 1}, {"taskUid": 2}, live]
    )
    transport.task_statuses[1] = ["processing", "succeeded"]

    result = _adapter(transport=transport).reconcile()

    assert result.changed
    assert sleeps == [0.1]


def test_reconcile_times_out_on_a_stuck_task(monkeypatch):
    """A task that never finishes fails the lifecycle call."""
    monkeypatch.setattr("openedx_search.meilisearch.TASK_TIMEOUT", 0)
    monkeypatch.setattr("openedx_search.meilisearch.time.sleep", lambda seconds: None)
    transport = FakeTransport(
        [TransportError("missing", status=404), {"taskUid": 1}],
        tasks={1: "processing"},
    )

    with pytest.raises(AdapterError, match="did not finish"):
        _adapter(transport=transport).reconcile()


def test_abort_rebuild_propagates_a_transport_failure():
    """Only a missing target is tolerated."""
    target = RebuildTarget(index="studio_content", version=1, generation="gen1")
    transport = FakeTransport([TransportError("unavailable", status=503)])

    with pytest.raises(TransportError):
        _adapter(transport=transport).abort_rebuild(target)


def test_begin_rebuild_rejects_an_invalid_index_list():
    """The index list endpoint must return a results list."""
    target = RebuildTarget(index="studio_content", version=1, generation="gen1")
    transport = FakeTransport([{}])

    with pytest.raises(AdapterError, match="invalid index list"):
        _adapter(transport=transport).begin_rebuild(target)


def test_index_list_is_paged():
    """A full page is followed by the next offset."""
    target = RebuildTarget(index="studio_content", version=1, generation="gen1")
    first = {"results": [{"uid": f"other-{position}"} for position in range(PAGE_SIZE)]}
    second = {"results": []}
    transport = FakeTransport([first, second, {"taskUid": 1}, {"taskUid": 2}])

    _adapter(transport=transport).begin_rebuild(target)

    assert transport.paths()[:2] == [
        ("GET", "/indexes?offset=0&limit=1000"),
        ("GET", f"/indexes?offset={PAGE_SIZE}&limit=1000"),
    ]
    assert transport.requests[2][2] == {
        "uid": "studio_content_v1_gen1",
        "primaryKey": "id",
    }


def test_upsert_rejects_an_acknowledgment_without_a_task():
    """A write must return a task id."""
    transport = FakeTransport([{}])

    with pytest.raises(AdapterError, match="did not acknowledge"):
        _adapter(transport=transport).upsert([{"id": "doc"}])


def test_upsert_renders_datetimes_inside_lists():
    """A datetime nested in an array becomes POSIX seconds too."""
    transport = FakeTransport([{"taskUid": 1}])

    _adapter(transport=transport).upsert([{"id": "doc", "tags": ["a", MOMENT]}])

    assert transport.requests[0][2] == [{"id": "doc", "tags": ["a", MOMENT_SECONDS]}]


def test_register_resolves_the_adapter():
    """Registering the engine lets the library resolve it from settings."""
    register()
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={
            "default": {
                "engine": "meilisearch",
                "url": "http://engine:7700",
                "api_key": "secret",
            }
        },
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        adapter = get_adapter(STUDIO_COURSE)

    assert isinstance(adapter, MeilisearchAdapter)
    assert adapter.backend.engine == "meilisearch"
