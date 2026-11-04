"""Tests for the adapter contract types."""

from openedx_search.adapters import CredentialScope, FilterTerm, RebuildTarget


def test_credential_scope_defaults_to_no_filters():
    """A scope without tenant filters is valid."""
    assert not CredentialScope().filters


def test_credential_scope_holds_tenant_filters():
    """The scope carries typed filter terms, not raw engine strings."""
    term = FilterTerm("org", ("openedx",))
    scope = CredentialScope(filters=(term,))
    assert scope.filters == (term,)


def test_credential_scope_freezes_filters():
    """A list passed at construction is frozen into a tuple."""
    term = FilterTerm("org", ("openedx",))
    scope = CredentialScope(filters=[term])
    assert scope.filters == (term,)


def test_rebuild_target_name_includes_a_generation():
    """Two rebuilds at the same version never share a target name."""
    first = RebuildTarget(index="studio_content", version=2, generation="20261104")
    second = RebuildTarget(index="studio_content", version=2, generation="20261105")

    assert first.name == "studio_content_v2_20261104"
    assert first.name != second.name
