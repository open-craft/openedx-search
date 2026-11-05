"""Tests for the adapter contract types."""

from datetime import datetime, timezone

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


def test_filter_group_rejects_a_bare_string():
    """A string would be split into characters, one per term."""
    with pytest.raises(ValueError, match="not a string"):
        FilterGroup("org")  # type: ignore[arg-type]


def test_credential_scope_rejects_a_bare_string():
    """A string would become one filter group per character."""
    with pytest.raises(ValueError, match="not a string"):
        CredentialScope(filters="org")  # type: ignore[arg-type]


def test_credential_scope_requires_filter_groups():
    """The scope holds FilterGroup values, not terms or strings."""
    with pytest.raises(ValueError, match="FilterGroup"):
        CredentialScope(filters=("org",))  # type: ignore[arg-type]


def test_rebuild_target_name_includes_a_generation():
    """Two rebuilds at the same version never share a target name."""
    first = RebuildTarget(index="studio_content", version=2, generation="20261104")
    second = RebuildTarget(index="studio_content", version=2, generation="20261105")

    assert first.name == "studio_content_v2_20261104"
    assert first.name != second.name


@pytest.mark.parametrize(
    ("index", "version", "generation"),
    [
        ("studio content", 2, "20261104"),
        ("studio_content", 0, "20261104"),
        ("studio_content", True, "20261104"),
        ("studio_content", 2, ""),
        ("studio_content", 2, "2026-11-04T10:00"),
    ],
)
def test_rebuild_target_rejects_invalid_parts(index, version, generation):
    """A bad part would render an index name the engine rejects."""
    with pytest.raises(ValueError):
        RebuildTarget(index=index, version=version, generation=generation)


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


def test_filter_term_rejects_a_bare_string():
    """A string would be split into characters, silently changing the filter."""
    with pytest.raises(ValueError, match="not a string"):
        FilterTerm("org", "openedx")


def test_filter_term_rejects_bytes():
    """Bytes would be split into integers, silently changing the filter."""
    with pytest.raises(ValueError, match="not a string"):
        FilterTerm("org", b"openedx")


def test_filter_term_rejects_a_mapping():
    """A mapping would be split into its keys."""
    with pytest.raises(ValueError, match="not a mapping"):
        FilterTerm("org", {"openedx": 1})


def test_filter_term_rejects_a_non_sequence():
    """A scalar is not a list of values."""
    with pytest.raises(ValueError, match="must be a sequence"):
        FilterTerm("org", 1)


def test_filter_term_keeps_a_value_sequence():
    """A tuple is the supported form."""
    assert FilterTerm("org", ("openedx",)).values == ("openedx",)


def test_filter_term_accepts_bool_and_float():
    """Boolean and numeric fields are filterable, and datetimes are floats."""
    term = FilterTerm("last_published", (1680674828.0, True))
    assert term.values == (1680674828.0, True)


def test_filter_term_keeps_an_aware_datetime():
    """Rendering converts a datetime to POSIX seconds, where the field type is known."""
    moment = datetime(2023, 4, 5, 6, 7, 8, tzinfo=timezone.utc)
    term = FilterTerm("last_published", (moment,))
    assert term.values == (moment,)


def test_filter_term_rejects_a_naive_datetime():
    """A naive datetime would use the local timezone, which is ambiguous."""
    with pytest.raises(ValueError, match="timezone-aware"):
        FilterTerm("last_published", (datetime(2023, 4, 5, 6, 7, 8),))


def test_filter_term_rejects_none():
    """Documents carry no nulls, so a null filter value has no meaning."""
    with pytest.raises(ValueError, match="invalid filter value"):
        FilterTerm("last_published", (None,))
