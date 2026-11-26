"""Tests for the consumer API contracts."""

from datetime import timedelta

import pytest
from django.test import override_settings

from openedx_search.adapters import (
    CredentialScope,
    FilterGroup,
    FilterTerm,
    SearchCredential,
    SearchQuery,
    SearchResult,
    WriteReceipt,
    register_adapter,
)
from openedx_search.api import DocumentError, check_write, delete, mint_credential, search, upsert
from openedx_search.config import BackendConfig
from openedx_search.registry import UseCaseNotRegistered, register_use_case
from tests.fakes import SIMPLE_REGISTRATION, FakeAdapter
from tests.fixtures import STUDIO_COURSE

# The fixture name is also the argument name pytest resolves, so every test
# that takes it shadows the fixture function.
# pylint: disable=redefined-outer-name

FAKE_BACKENDS = {
    "default": {"engine": "fake", "url": "http://fake", "api_key": "secret"},
}


@pytest.fixture
def fake_adapter():
    """Register the simple use case and a recording adapter for it."""
    register_use_case(SIMPLE_REGISTRATION)
    adapter = FakeAdapter(
        SIMPLE_REGISTRATION,
        BackendConfig(name="default", engine="fake", url="http://fake", api_key="secret"),
    )
    register_adapter("fake", lambda registration, backend: adapter)
    with override_settings(
        OPENEDX_SEARCH_BACKENDS=FAKE_BACKENDS,
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        yield adapter


def test_mint_credential_delegates(fake_adapter):
    """The scope and lifetime reach the adapter and its credential is returned."""
    scope = CredentialScope(filters=(FilterGroup((FilterTerm("org", ("openedx",)),)),))

    credential = mint_credential("simple", scope, timedelta(hours=1))

    assert credential == SearchCredential(url="http://fake", index_name="simple_index", api_key="key")
    assert fake_adapter.calls[-1] == ("mint_credential", scope, timedelta(hours=1))


def test_mint_credential_rejects_a_raw_scope(fake_adapter):
    """A filter sequence is not a CredentialScope."""
    with pytest.raises(ValueError, match="CredentialScope"):
        mint_credential("simple", (), timedelta(hours=1))

    assert fake_adapter.calls == []


def test_mint_credential_rejects_a_non_timedelta_expiry(fake_adapter):
    """A number of seconds is not a lifetime."""
    with pytest.raises(ValueError, match="timedelta"):
        mint_credential("simple", CredentialScope(), 3600)

    assert fake_adapter.calls == []


def test_mint_credential_rejects_non_positive_expiry(fake_adapter):
    """A credential must have a positive lifetime."""
    with pytest.raises(ValueError, match="must be positive"):
        mint_credential("simple", CredentialScope(), timedelta(0))

    assert fake_adapter.calls == []


@pytest.mark.usefixtures("fake_adapter")
def test_mint_credential_unknown_use_case():
    """The registration must exist."""
    with pytest.raises(UseCaseNotRegistered):
        mint_credential("missing", CredentialScope(), timedelta(hours=1))


def test_upsert_validates_and_sends_the_batch(fake_adapter):
    """A valid batch reaches the adapter and returns its receipt."""
    receipt = upsert("simple", [{"id": "doc-1", "title": "Hello"}])

    assert receipt.complete
    assert fake_adapter.calls == [("upsert", ({"id": "doc-1", "title": "Hello"},), None)]


def test_upsert_rejects_invalid_documents(fake_adapter):
    """Validation happens before the adapter call."""
    with pytest.raises(DocumentError, match="required field 'title'"):
        upsert("simple", [{"id": "doc-1"}])

    assert fake_adapter.calls == []


@pytest.mark.usefixtures("fake_adapter")
def test_upsert_requires_a_batch():
    """A string is not a batch, and an empty batch is rejected."""
    with pytest.raises(ValueError, match="sequence"):
        upsert("simple", "not a batch")
    with pytest.raises(ValueError, match="not be empty"):
        upsert("simple", [])


@pytest.mark.usefixtures("fake_adapter")
def test_upsert_unknown_use_case():
    """The registration must exist."""
    with pytest.raises(UseCaseNotRegistered):
        upsert("missing", [{"id": "doc-1", "title": "Hello"}])


@pytest.mark.parametrize(
    ("primary_keys", "expected"),
    [
        ("doc-1", ("doc-1",)),
        (["doc-1", "doc-2"], ("doc-1", "doc-2")),
        (("doc-1",), ("doc-1",)),
    ],
)
def test_delete_by_primary_keys(fake_adapter, primary_keys, expected):
    """A key or a sequence is normalized to a tuple and reaches the adapter."""
    delete("simple", primary_keys)

    assert fake_adapter.calls[-1] == ("delete", expected, None, None)


@pytest.mark.parametrize(
    ("primary_keys", "message"),
    [
        ([], "must not be empty"),
        ("a:b", "invalid document id"),
        (["valid-id", "a:b"], "invalid document id"),
        ({"doc-1": "x"}, "string or a sequence"),
        (1, "string or a sequence"),
        (b"doc-1", "string or a sequence"),
        ("a" * 469, "maximum is 468"),
    ],
)
def test_delete_rejects_invalid_primary_keys(fake_adapter, primary_keys, message):
    """Nothing reaches the adapter when a key is invalid."""
    with pytest.raises(ValueError, match=message):
        delete("simple", primary_keys)

    assert fake_adapter.calls == []


def test_delete_by_filter(fake_adapter):
    """The abstract filter expression reaches the adapter unchanged."""
    expression = (FilterGroup((FilterTerm("id", ("doc-1",)),)),)

    delete("simple", filter=expression)

    assert fake_adapter.calls[-1] == ("delete", None, expression, None)


@pytest.mark.usefixtures("fake_adapter")
def test_delete_requires_exactly_one_form():
    """A delete has keys or a filter, never both and never neither."""
    with pytest.raises(ValueError, match="exactly one"):
        delete("simple")
    with pytest.raises(ValueError, match="exactly one"):
        delete(
            "simple",
            "doc-1",
            filter=(FilterGroup((FilterTerm("title", ("Hello",)),)),),
        )


@pytest.mark.usefixtures("fake_adapter")
def test_delete_rejects_a_string_filter():
    """A raw engine filter is not accepted."""
    with pytest.raises(ValueError, match="not a string"):
        delete("simple", filter="title = Hello")


@pytest.mark.usefixtures("fake_adapter")
def test_delete_rejects_an_empty_filter():
    """An empty filter would match everything."""
    with pytest.raises(ValueError, match="not be empty"):
        delete("simple", filter=())


@pytest.mark.parametrize(
    "term",
    [
        FilterTerm("title", ("Hello",)),
        FilterTerm("nope", ("x",)),
    ],
)
def test_filters_reject_a_field_the_schema_cannot_filter(fake_adapter, term):
    """A declared-but-unfilterable or undeclared field fails before the adapter."""
    expression = (FilterGroup((term,)),)

    with pytest.raises(ValueError, match=f"filter field {term.field!r}"):
        search("simple", SearchQuery(filters=expression))
    with pytest.raises(ValueError, match=f"filter field {term.field!r}"):
        delete("simple", filter=expression)

    assert fake_adapter.calls == []


def test_filters_accept_the_primary_key_and_a_filterable_nested_field(fake_adapter):
    """``id`` is always filterable and nested fields use their declared name."""
    register_use_case(STUDIO_COURSE)
    expression = (
        FilterGroup(
            (
                FilterTerm("id", ("doc-1",)),
                FilterTerm("tags.level0", ("openedx",)),
            )
        ),
    )

    search("studio_course", SearchQuery(filters=expression))
    delete("studio_course", filter=expression)

    assert [call[0] for call in fake_adapter.calls] == ["search", "delete"]


def test_check_write_delegates(fake_adapter):
    """One poll reaches the adapter and returns its receipt."""
    receipt = WriteReceipt(task_id=7)

    result = check_write("simple", receipt)

    assert result.complete
    assert fake_adapter.calls[-1] == ("check_write", receipt)


@pytest.mark.usefixtures("fake_adapter")
def test_check_write_rejects_a_non_receipt():
    """The function only accepts a receipt from a write."""
    with pytest.raises(ValueError, match="WriteReceipt"):
        check_write("simple", "receipt")


@pytest.mark.usefixtures("fake_adapter")
def test_check_write_unknown_use_case():
    """The registration must exist."""
    with pytest.raises(UseCaseNotRegistered):
        check_write("missing", WriteReceipt())


def test_search_delegates_the_query_to_the_adapter(fake_adapter):
    """The typed query reaches the adapter unchanged and its result is returned."""
    query = SearchQuery(text="hello", limit=5)

    result = search("simple", query)

    assert fake_adapter.calls == [("search", query)]
    assert result == SearchResult(documents=(), total=0)


def test_search_requires_a_typed_query(fake_adapter):
    """A raw mapping is not a query, so nothing reaches the adapter."""
    with pytest.raises(ValueError, match="SearchQuery"):
        search("simple", {"text": "hello"})

    assert fake_adapter.calls == []
