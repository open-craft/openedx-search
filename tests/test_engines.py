"""Tests for the experimental read-only engine search slice."""

# pylint: disable=redefined-outer-name

import json
import urllib.request

import pytest

from openedx_search.adapters import FilterGroup, FilterTerm, SearchQuery
from openedx_search.config import BackendConfig
from openedx_search.engines import (
    EngineError,
    UnsupportedFilter,
    render_meilisearch_filter,
    render_typesense_filter,
    run_search,
)
from tests.fixtures import STUDIO_COURSE_SCHEMA

COURSE_KEY = "course-v1:OpenedX+DemoX+DemoCourse"
CHAPTER_KEY = "block-v1:OpenedX+DemoX+DemoCourse+type@chapter+block@30b3fbb840024953b2d4b2e700a53002"
SEQUENTIAL_KEY = "block-v1:OpenedX+DemoX+DemoCourse+type@sequential+block@4e1de5e13fc3422997fe246b40a43aa1"

MIGRATOR_FILTER = (
    FilterGroup((FilterTerm("context_key", (COURSE_KEY,)),)),
    FilterGroup((FilterTerm("breadcrumbs.usage_key", (CHAPTER_KEY, SEQUENTIAL_KEY)),)),
)


class _Response:
    """A minimal stand-in for ``urlopen``'s response."""

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        """Return the JSON body."""
        return json.dumps(self._payload).encode()

    def __enter__(self):
        """Support the context manager form."""
        return self

    def __exit__(self, *args):
        """Do not suppress exceptions."""
        return False


@pytest.fixture
def requests_seen(monkeypatch):
    """Capture requests and answer as the selected engine would."""
    seen = []

    def fake_urlopen(request, *args, **kwargs):
        """Record one request and return a canned engine response."""
        seen.append({"url": request.full_url, "body": request.data, "headers": dict(request.header_items())})
        if request.data is not None:
            return _Response({"hits": [{"id": "a"}], "estimatedTotalHits": 7})
        return _Response({"found": 7, "hits": [{"document": {"id": "a"}}]})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def _backend(engine):
    """Build a backend config for one engine."""
    return BackendConfig(name="test", engine=engine, url="http://engine:1234", api_key="key")


@pytest.mark.parametrize(
    "rendered",
    [
        render_meilisearch_filter(MIGRATOR_FILTER, STUDIO_COURSE_SCHEMA),
        render_typesense_filter(MIGRATOR_FILTER, STUDIO_COURSE_SCHEMA),
    ],
)
def test_migrator_filter_keeps_both_groups(rendered):
    """The real caller's two AND groups and the usage key list survive rendering."""
    assert COURSE_KEY in rendered
    assert CHAPTER_KEY in rendered and SEQUENTIAL_KEY in rendered
    assert ("AND" in rendered) or ("&&" in rendered)


def test_meilisearch_render_is_exact():
    """Meilisearch clauses are equality and IN, with quoted string literals."""
    assert render_meilisearch_filter(MIGRATOR_FILTER) == (
        f'context_key = "{COURSE_KEY}" AND breadcrumbs.usage_key IN ["{CHAPTER_KEY}", "{SEQUENTIAL_KEY}"]'
    )


def test_typesense_render_is_exact():
    """Typesense clauses use := and backtick literals, and && between groups."""
    assert render_typesense_filter(MIGRATOR_FILTER) == (
        f"context_key:=`{COURSE_KEY}` && breadcrumbs.usage_key:=[`{CHAPTER_KEY}`, `{SEQUENTIAL_KEY}`]"
    )


@pytest.mark.parametrize("render", [render_meilisearch_filter, render_typesense_filter])
def test_or_group_and_id_filter_are_preserved(render):
    """Terms in one group stay alternatives; a second group is another clause."""
    expression = (
        FilterGroup((FilterTerm("org", ("openedx", "edx")), FilterTerm("id", ("abc-1",)))),
        FilterGroup((FilterTerm("access_id", (1,)),)),
    )
    rendered = render(expression)
    assert ("OR" in rendered) or ("||" in rendered)
    assert "openedx" in rendered and "edx" in rendered and "abc-1" in rendered
    assert ("AND" in rendered) or ("&&" in rendered)


@pytest.mark.parametrize(
    "field", [("nope", UnsupportedFilter), ("display_name", UnsupportedFilter), ("description", UnsupportedFilter)]
)
def test_rejects_undeclared_or_non_filterable_fields(field):
    """A schema is enforced before the request, not left to the engine."""
    name, error = field
    with pytest.raises(error):
        render_meilisearch_filter((FilterGroup((FilterTerm(name, ("x",)),)),), STUDIO_COURSE_SCHEMA)


@pytest.mark.parametrize("render", [render_meilisearch_filter, render_typesense_filter])
def test_rejects_control_characters(render):
    """A newline would be filter syntax, so it is rejected instead of escaped."""
    with pytest.raises(UnsupportedFilter):
        render((FilterGroup((FilterTerm("context_key", ("a\nb",)),)),))


def test_typesense_rejects_backtick_in_value():
    """The prototype does not guess Typesense's backtick escapes."""
    with pytest.raises(UnsupportedFilter):
        render_typesense_filter((FilterGroup((FilterTerm("context_key", ("a`b",)),)),))


def test_query_is_bounded():
    """The page size has a floor and a ceiling; count-only is explicit."""
    with pytest.raises(ValueError, match="limit must be between"):
        SearchQuery(limit=0)
    with pytest.raises(ValueError, match="limit must be between"):
        SearchQuery(limit=101)
    assert SearchQuery(count_only=True).count_only is True


def test_meilisearch_count_only_is_inexact(requests_seen):
    """limit 0 sends no hits and never claims the estimate is exact."""
    result = run_search(_backend("meilisearch"), "studio_course", STUDIO_COURSE_SCHEMA, SearchQuery(
        filters=MIGRATOR_FILTER, count_only=True
    ))
    assert json.loads(requests_seen[0]["body"])["limit"] == 0
    assert result.documents == ()
    assert (result.total, result.total_is_exact) == (7, False)


def test_typesense_count_only_is_exact(requests_seen):
    """found is exact, and the count-only hit is dropped."""
    result = run_search(_backend("typesense"), "studio_course", STUDIO_COURSE_SCHEMA, SearchQuery(
        filters=MIGRATOR_FILTER, count_only=True
    ))
    assert "per_page=1" in requests_seen[0]["url"]
    assert result.documents == ()
    assert (result.total, result.total_is_exact) == (7, True)


def test_typesense_bounded_hits_return_documents(requests_seen):
    """A non-count query returns the documents from the engine hits."""
    result = run_search(_backend("typesense"), "studio_course", STUDIO_COURSE_SCHEMA, SearchQuery(
        filters=MIGRATOR_FILTER, limit=5
    ))
    assert "per_page=5" in requests_seen[0]["url"]
    assert result.documents == ({"id": "a"},)


def test_unknown_engine_has_no_read_path():
    """An engine without a read-only path fails instead of guessing."""
    with pytest.raises(EngineError, match="no read-only search path"):
        run_search(_backend("algolia"), "studio_course", STUDIO_COURSE_SCHEMA, SearchQuery())
