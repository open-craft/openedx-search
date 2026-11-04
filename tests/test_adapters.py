"""Tests for the adapter contract types."""

import pytest

from openedx_search.adapters import CredentialScope, FilterGroup, FilterTerm, RebuildTarget, SearchCredential


def test_credential_scope_defaults_to_no_filters():
    """A scope without tenant filters is valid."""
    assert not CredentialScope().filters


def test_credential_scope_holds_tenant_filters():
    """The scope carries typed filter groups, not raw engine strings."""
    group = FilterGroup((FilterTerm("org", ("openedx",)),))
    scope = CredentialScope(filters=(group,))
    assert scope.filters == (group,)


def test_credential_scope_freezes_filters():
    """A list passed at construction is frozen into a tuple."""
    group = FilterGroup((FilterTerm("org", ("openedx",)),))
    scope = CredentialScope(filters=[group])
    assert scope.filters == (group,)


def test_tenant_filter_is_one_or_group():
    """Studio's filter is org OR access_id, not two ANDed terms."""
    expression = (
        FilterGroup(
            (
                FilterTerm("org", ("openedx",)),
                FilterTerm("access_id", ("1",)),
            )
        ),
    )
    scope = CredentialScope(filters=expression)

    assert len(scope.filters) == 1
    assert len(scope.filters[0].terms) == 2


def test_filter_groups_are_anded():
    """Separate groups narrow the result set."""
    expression = (
        FilterGroup((FilterTerm("org", ("openedx",)),)),
        FilterGroup((FilterTerm("type", ("course_block",)),)),
    )
    assert len(CredentialScope(filters=expression).filters) == 2


def test_filter_group_needs_a_term():
    """An empty group would render as an empty OR."""
    with pytest.raises(ValueError, match="at least one term"):
        FilterGroup(())


def test_filter_group_rejects_bare_terms():
    """A group holds FilterTerm values, not tuples or strings."""
    with pytest.raises(ValueError, match="FilterTerm"):
        FilterGroup(("org",))  # type: ignore[arg-type]


def test_rebuild_target_name_includes_a_generation():
    """Two rebuilds at the same version never share a target name."""
    first = RebuildTarget(index="studio_content", version=2, generation="20261104")
    second = RebuildTarget(index="studio_content", version=2, generation="20261105")

    assert first.name == "studio_content_v2_20261104"
    assert first.name != second.name


def test_search_credential_carries_browser_fields():
    """The browser needs the public URL, the physical index and the key."""
    credential = SearchCredential(
        url="https://search.example.com",
        index_name="studio_content_v2",
        api_key="scoped-key",
    )

    assert credential.url == "https://search.example.com"
    assert credential.index_name == "studio_content_v2"
    assert credential.api_key == "scoped-key"


def test_search_credential_requires_every_field():
    """A credential without an index name cannot be used by the browser."""
    with pytest.raises(ValueError, match="index_name"):
        SearchCredential(url="https://search.example.com", index_name="", api_key="scoped-key")
