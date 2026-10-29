"""Tests for the consumer API contracts."""

from datetime import timedelta

import pytest

from openedx_search.adapters import CredentialScope
from openedx_search.api import _normalize_primary_keys, mint_credential


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
