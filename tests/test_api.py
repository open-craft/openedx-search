"""Tests for the consumer API contracts."""

from datetime import timedelta

import pytest
from django.test import override_settings

from openedx_search.adapters import CredentialScope, FilterGroup, FilterTerm, register_adapter
from openedx_search.api import DocumentError, _normalize_primary_keys, delete, mint_credential, upsert
from openedx_search.config import BackendConfig
from openedx_search.registry import UseCaseNotRegistered, register_use_case
from tests.fakes import SIMPLE_REGISTRATION, FakeAdapter

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


def test_mint_credential_rejects_non_positive_expiry():
    """A credential must have a positive lifetime."""
    with pytest.raises(ValueError, match="must be positive"):
        mint_credential("studio_course", CredentialScope(), timedelta(0))


def test_normalize_single_primary_key():
    """A string is one key, not a sequence of characters."""
    assert _normalize_primary_keys("abc") == ("abc",)


def test_normalize_primary_key_sequence():
    """A sequence is normalized to a tuple."""
    assert _normalize_primary_keys(["a", "b"]) == ("a", "b")


def test_normalize_empty_primary_keys():
    """An empty sequence cannot become a delete."""
    with pytest.raises(ValueError, match="must not be empty"):
        _normalize_primary_keys([])


def test_normalize_missing_primary_keys():
    """None means the caller used the filter form."""
    assert _normalize_primary_keys(None) is None


def test_normalize_rejects_invalid_document_id():
    """A colon fails the engines, so it fails before the adapter call."""
    with pytest.raises(ValueError, match="invalid document id"):
        _normalize_primary_keys("block-v1:edX+DemoX+type@html+block@abc")


def test_normalize_rejects_invalid_document_id_in_sequence():
    """Every key in a batch is validated."""
    with pytest.raises(ValueError, match="invalid document id"):
        _normalize_primary_keys(["valid-id", "block-v1:edX+DemoX"])


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


def test_upsert_requires_a_batch(fake_adapter):
    """A string is not a batch, and an empty batch is rejected."""
    with pytest.raises(ValueError, match="sequence"):
        upsert("simple", "not a batch")
    with pytest.raises(ValueError, match="not be empty"):
        upsert("simple", [])


def test_upsert_unknown_use_case(fake_adapter):
    """The registration must exist."""
    with pytest.raises(UseCaseNotRegistered):
        upsert("missing", [{"id": "doc-1", "title": "Hello"}])


def test_delete_by_primary_keys(fake_adapter):
    """Keys are normalized and passed as a tuple."""
    delete("simple", ["doc-1", "doc-2"])

    assert fake_adapter.calls[-1] == ("delete", ("doc-1", "doc-2"), None, None)


def test_delete_by_filter(fake_adapter):
    """The abstract filter expression reaches the adapter unchanged."""
    expression = (FilterGroup((FilterTerm("title", ("Hello",)),)),)

    delete("simple", filter=expression)

    assert fake_adapter.calls[-1] == ("delete", None, expression, None)


def test_delete_requires_exactly_one_form(fake_adapter):
    """A delete has keys or a filter, never both and never neither."""
    with pytest.raises(ValueError, match="exactly one"):
        delete("simple")
    with pytest.raises(ValueError, match="exactly one"):
        delete(
            "simple",
            "doc-1",
            filter=(FilterGroup((FilterTerm("title", ("Hello",)),)),),
        )


def test_delete_rejects_a_string_filter(fake_adapter):
    """A raw engine filter is not accepted."""
    with pytest.raises(ValueError, match="not a string"):
        delete("simple", filter="title = Hello")


def test_delete_rejects_an_empty_filter(fake_adapter):
    """An empty filter would match everything."""
    with pytest.raises(ValueError, match="not be empty"):
        delete("simple", filter=())
