"""
Read-only search against an existing engine index (experimental slice).

This module implements only the read path: it renders a ``FilterExpression`` in
each engine's filter syntax, sends one search request and maps the response to
:class:`~openedx_search.adapters.SearchResult`. Lifecycle, writes, credentials
and index provisioning are deliberately absent, so ``index`` must already exist
under its logical name on the deployment's engine.

Only the abstract filter expression is accepted. A term the schema does not
declare filterable, or a value the engine cannot represent, raises
:class:`UnsupportedFilter`` instead of being reinterpreted as filter syntax.

Count exactness is reported, not guessed: Meilisearch's ``estimatedTotalHits``
is an estimate capped by the index's ``maxTotalHits``, while Typesense's
``found`` is exact.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Callable

from .adapters import AdapterError, FilterExpression, FilterGroup, FilterTerm, SearchQuery, SearchResult
from .config import BackendConfig
from .schema import IndexSchema

__all__ = [
    "EngineError",
    "REQUEST_TIMEOUT_SECONDS",
    "UnsupportedFilter",
    "render_meilisearch_filter",
    "render_typesense_filter",
    "run_search",
]

REQUEST_TIMEOUT_SECONDS = 10


class UnsupportedFilter(AdapterError):
    """A filter term or value cannot be represented on this engine."""


class EngineError(AdapterError):
    """The engine rejected the request, was unreachable, or has no read path."""


def _reject_control_characters(field: str, value: str) -> None:
    """Reject values whose control characters an engine would read as syntax."""
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise UnsupportedFilter(f"filter value for {field!r} contains a control character")


def _check_term(term: FilterTerm, schema: IndexSchema | None) -> None:
    """Require a declared, filterable field when a schema is available."""
    if schema is None:
        return
    try:
        declared = schema.field(term.field)
    except KeyError:
        raise UnsupportedFilter(f"field {term.field!r} is not declared in the schema") from None
    if not declared.capabilities.filterable:
        raise UnsupportedFilter(f"field {term.field!r} is not declared filterable")


def _posix_seconds(value: datetime) -> int:
    """Render a timezone-aware datetime the way the engines store it."""
    return int(value.timestamp())


def _render_meilisearch_value(field: str, value: Any) -> str:
    """Render one literal for a Meilisearch filter expression."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return str(_posix_seconds(value))
    if isinstance(value, (int, float)):
        return str(value)
    _reject_control_characters(field, value)
    return json.dumps(value, ensure_ascii=False)


def _render_typesense_value(field: str, value: Any) -> str:
    """Render one literal for a Typesense ``filter_by`` expression."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return str(_posix_seconds(value))
    if isinstance(value, (int, float)):
        return str(value)
    _reject_control_characters(field, value)
    if "`" in value or "\\" in value:
        # The prototype does not claim Typesense's escape sequences for these.
        raise UnsupportedFilter(f"filter value for {field!r} contains a backtick or backslash")
    return f"`{value}`"


def _render_group(
    group: FilterGroup, schema: IndexSchema | None, value: Callable[[str, Any], str]
) -> list[tuple[str, list[str]]]:
    """Render one OR group as a list of ``(field, rendered values)`` clauses."""
    clauses = []
    for term in group.terms:
        _check_term(term, schema)
        clauses.append((term.field, [value(term.field, item) for item in term.values]))
    return clauses


def render_meilisearch_filter(expression: FilterExpression, schema: IndexSchema | None = None) -> str:
    """
    Render a filter expression as a Meilisearch ``filter`` string.

    Groups become ANDed parenthesised expressions and the terms inside a group
    become ORed clauses. An empty expression renders as an empty string, which
    callers omit from the request.
    """
    groups = []
    for group in expression:
        clauses = _render_group(group, schema, _render_meilisearch_value)
        rendered = [f"{field} = {values[0]}" if len(values) == 1 else f"{field} IN [{', '.join(values)}]"
                    for field, values in clauses]
        groups.append(f"({' OR '.join(rendered)})" if len(rendered) > 1 else rendered[0])
    return " AND ".join(groups)


def render_typesense_filter(expression: FilterExpression, schema: IndexSchema | None = None) -> str:
    """
    Render a filter expression as a Typesense ``filter_by`` string.

    Groups become ``&&``-joined parenthesised expressions and the terms inside a
    group become ``||``-joined clauses. String literals are backtick-quoted, so a
    course or usage key keeps its punctuation instead of being read as syntax.
    """
    groups = []
    for group in expression:
        clauses = _render_group(group, schema, _render_typesense_value)
        rendered = [f"{field}:=[{', '.join(values)}]" if len(values) > 1 else f"{field}:={values[0]}"
                    for field, values in clauses]
        groups.append(f"({' || '.join(rendered)})" if len(rendered) > 1 else rendered[0])
    return " && ".join(groups)


def _read_json(request: urllib.request.Request) -> Any:
    """Send one request and decode its JSON body, mapping failures to ``EngineError``."""
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise EngineError(f"{request.full_url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise EngineError(f"{request.full_url} is unreachable: {exc.reason}") from exc
    except ValueError as exc:
        raise EngineError(f"{request.full_url} returned invalid JSON") from exc


def _search_meilisearch(
    backend: BackendConfig, index: str, schema: IndexSchema | None, query: SearchQuery
) -> SearchResult:
    """Search one Meilisearch index; ``count_only`` sends ``limit: 0``."""
    payload: dict[str, Any] = {"limit": 0 if query.count_only else query.limit}
    filter_string = render_meilisearch_filter(query.filters, schema)
    if filter_string:
        payload["filter"] = filter_string
    request = urllib.request.Request(
        f"{backend.url.rstrip('/')}/indexes/{urllib.parse.quote(index)}/search",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {backend.api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    response = _read_json(request)
    return SearchResult(
        documents=() if query.count_only else tuple(response.get("hits", ())),
        total=int(response["estimatedTotalHits"]),
        total_is_exact=False,
    )


def _search_typesense(
    backend: BackendConfig, index: str, schema: IndexSchema | None, query: SearchQuery
) -> SearchResult:
    """Search one Typesense collection; ``found`` is exact, ``count_only`` drops the hit."""
    params = {"q": "*", "per_page": 1 if query.count_only else query.limit}
    filter_string = render_typesense_filter(query.filters, schema)
    if filter_string:
        params["filter_by"] = filter_string
    collection = urllib.parse.quote(index)
    request = urllib.request.Request(
        f"{backend.url.rstrip('/')}/collections/{collection}/documents/search?{urllib.parse.urlencode(params)}",
        headers={"X-TYPESENSE-API-KEY": backend.api_key},
        method="GET",
    )
    response = _read_json(request)
    documents = () if query.count_only else tuple(hit["document"] for hit in response.get("hits", ()))
    return SearchResult(documents=documents, total=int(response["found"]), total_is_exact=True)


_ENGINES = {"meilisearch": _search_meilisearch, "typesense": _search_typesense}


def run_search(
    backend: BackendConfig, index: str, schema: IndexSchema | None, query: SearchQuery
) -> SearchResult:
    """
    Search ``index`` on ``backend`` with the engine's read-only path.

    The index is used as-is: nothing is created and no settings are applied.
    """
    try:
        searcher = _ENGINES[backend.engine.lower()]
    except KeyError:
        raise EngineError(f"no read-only search path for engine {backend.engine!r}") from None
    return searcher(backend, index, schema, query)
