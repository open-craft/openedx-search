"""Tests for the consumer API contracts."""

from datetime import timedelta

import pytest

from openedx_search.adapters import CredentialScope
from openedx_search.api import mint_credential


def test_mint_credential_rejects_non_positive_expiry():
    """A credential must have a positive lifetime."""
    with pytest.raises(ValueError, match="must be positive"):
        mint_credential("studio_course", CredentialScope(), timedelta(0))
