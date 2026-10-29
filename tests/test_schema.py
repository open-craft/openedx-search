"""Tests for the engine-neutral schema model."""

import pytest

from openedx_search.schema import Capabilities, Field, FieldType, IndexSchema, SchemaError, Searchable
from tests.fixtures import STUDIO_COURSE_SCHEMA, STUDIO_LIBRARY_SCHEMA


def test_course_schema_is_valid():
    """The course schema covers only the course document shape."""
    assert STUDIO_COURSE_SCHEMA.name == "studio_course"
    assert len(STUDIO_COURSE_SCHEMA.fields) == 21
    assert STUDIO_COURSE_SCHEMA.field("display_name").capabilities.searchable.order == 1
    assert STUDIO_COURSE_SCHEMA.field("tags.level3").capabilities.facetable is True
    assert STUDIO_COURSE_SCHEMA.field("content").additional_properties is True
    assert STUDIO_COURSE_SCHEMA.field("access_id").capabilities.filterable is True


def test_library_schema_is_valid():
    """The library schema is the union of the three library document shapes."""
    assert STUDIO_LIBRARY_SCHEMA.name == "studio_library"
    assert len(STUDIO_LIBRARY_SCHEMA.fields) == 47
    assert STUDIO_LIBRARY_SCHEMA.field("created").type is FieldType.DATETIME
    assert STUDIO_LIBRARY_SCHEMA.field("num_children").type is FieldType.INT32
    assert STUDIO_LIBRARY_SCHEMA.field("units.key").array is True
    assert STUDIO_LIBRARY_SCHEMA.field("content.child_usage_keys").array is True
    assert STUDIO_LIBRARY_SCHEMA.field("published.num_children").type is FieldType.INT32


def test_course_schema_omits_library_only_fields():
    """Course blocks do not carry collections, containers or publish state."""
    course_fields = {entry.name for entry in STUDIO_COURSE_SCHEMA.fields}
    library_fields = {entry.name for entry in STUDIO_LIBRARY_SCHEMA.fields}

    library_only = {
        "collections",
        "collections.display_name",
        "collections.key",
        "units",
        "units.display_name",
        "units.key",
        "subsections",
        "subsections.display_name",
        "subsections.key",
        "sections",
        "sections.display_name",
        "sections.key",
        "created",
        "last_published",
        "publish_status",
        "num_children",
        "published",
        "published.display_name",
        "published.description",
        "published.num_children",
        "content.child_usage_keys",
        "content.child_display_names",
    }

    assert library_only <= library_fields
    assert not library_only & course_fields
    assert course_fields <= library_fields


def test_field_defaults():
    """A bare field is a string with no capability flags set."""
    entry = Field("title")
    assert entry.type is FieldType.STRING
    assert entry.required is False
    assert entry.capabilities.searchable is False
    assert entry.capabilities.filterable is False
    assert entry.capabilities.facetable is False
    assert entry.capabilities.sortable is False


def test_sort_overrides_relevance_defaults_to_true():
    """A schema that does not set the flag puts an explicit sort ahead of relevance."""
    schema = IndexSchema(
        name="default_sort_precedence",
        fields=(Field("id", required=True),),
    )
    assert schema.sort_overrides_relevance is True


def test_sort_overrides_relevance_can_be_disabled():
    """A use case can keep relevance ahead of an explicit sort."""
    schema = IndexSchema(
        name="relevance_first",
        fields=(Field("id", required=True),),
        sort_overrides_relevance=False,
    )
    assert schema.sort_overrides_relevance is False


def test_sort_overrides_relevance_must_be_bool():
    """The flag is a boolean, not an engine-specific string."""
    with pytest.raises(SchemaError, match="must be a bool"):
        IndexSchema(
            name="bad_flag",
            fields=(Field("id", required=True),),
            sort_overrides_relevance="yes",
        )


def test_searchable_field_must_be_string_or_object():
    """Typesense's ``query_by`` only searches string leaves."""
    with pytest.raises(SchemaError, match="must be a string or object"):
        Field("access_id", FieldType.INT64, capabilities=Capabilities(searchable=True))

    entry = Field("title", FieldType.STRING, capabilities=Capabilities(searchable=True))
    assert entry.capabilities.searchable is True

    content = Field(
        "content",
        FieldType.OBJECT,
        additional_properties=True,
        capabilities=Capabilities(searchable=True),
    )
    assert content.capabilities.searchable is True


def test_required_fields_are_marked():
    """Fields the content/search builders always provide are required."""
    assert STUDIO_COURSE_SCHEMA.field("id").required is True
    assert STUDIO_COURSE_SCHEMA.field("block_type").required is True
    assert STUDIO_COURSE_SCHEMA.field("modified").required is False

    assert STUDIO_LIBRARY_SCHEMA.field("type").required is True
    assert STUDIO_LIBRARY_SCHEMA.field("created").required is True
    assert STUDIO_LIBRARY_SCHEMA.field("block_type").required is False


def test_facetable_implies_filterable():
    """Facets are filters in every supported engine."""
    capabilities = Capabilities(facetable=True)
    assert capabilities.filterable is True


def test_duplicate_field_names_are_rejected():
    """Two fields cannot share a path."""
    with pytest.raises(SchemaError, match="duplicate field name"):
        IndexSchema(name="duplicate", fields=(Field("id"), Field("id")))


def test_id_field_must_be_declared():
    """A schema must declare the required string id."""
    with pytest.raises(SchemaError, match="required string field 'id'"):
        IndexSchema(name="missing_id", fields=(Field("other", required=True),))


def test_id_field_must_be_string():
    """Typesense and Meilisearch both use a string id."""
    with pytest.raises(SchemaError, match="field 'id' must be a string"):
        IndexSchema(name="bad_id", fields=(Field("id", FieldType.INT32, required=True),))


def test_id_field_must_be_required():
    """The document id cannot be omitted."""
    with pytest.raises(SchemaError, match="field 'id' must be required"):
        IndexSchema(name="bad_id", fields=(Field("id", FieldType.STRING),))


def test_id_field_cannot_be_an_array():
    """The document id is a single string."""
    with pytest.raises(SchemaError, match="field 'id' cannot be an array"):
        IndexSchema(
            name="bad_id",
            fields=(Field("id", FieldType.STRING, required=True, array=True),),
        )


def test_object_field_cannot_be_sortable():
    """Object fields have no sortable representation."""
    with pytest.raises(SchemaError, match="cannot be sortable"):
        Field("tags", FieldType.OBJECT, capabilities=Capabilities(sortable=True))


def test_array_field_cannot_be_sortable():
    """Typesense cannot sort array fields."""
    with pytest.raises(SchemaError, match="array field .* cannot be sortable"):
        Field(
            "tags.level0",
            FieldType.STRING,
            array=True,
            capabilities=Capabilities(sortable=True),
        )


def test_additional_properties_requires_object():
    """Only object fields can be free-form."""
    with pytest.raises(SchemaError, match="additional_properties requires an object"):
        Field("title", FieldType.STRING, additional_properties=True)


def test_nested_paths_are_allowed():
    """Nested fields use dot paths."""
    entry = Field("tags.level0", FieldType.STRING, array=True)
    assert entry.name == "tags.level0"


def test_bad_nested_path_is_rejected():
    """An empty path segment is invalid."""
    with pytest.raises(SchemaError, match="invalid field path"):
        Field("tags..level0")


def test_searchable_order_must_be_positive():
    """Searchable rank starts at one."""
    with pytest.raises(SchemaError, match="order must be >= 1"):
        Searchable(order=0)


def test_field_lookup_by_name():
    """``field`` returns the declared entry and raises for unknown names."""
    assert STUDIO_COURSE_SCHEMA.field("usage_key").type is FieldType.STRING
    with pytest.raises(KeyError):
        STUDIO_COURSE_SCHEMA.field("not_a_field")


def test_unknown_field_type_is_allowed():
    """Fields parsed from untyped engines keep an explicit unknown type."""
    entry = Field("mystery", FieldType.UNKNOWN)
    assert entry.type is FieldType.UNKNOWN

    sortable = Field(
        "mystery",
        FieldType.UNKNOWN,
        capabilities=Capabilities(sortable=True),
    )
    assert sortable.capabilities.sortable is True
