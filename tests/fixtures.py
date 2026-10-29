"""
Sample schemas and registrations used to validate the model and registration API.

They are loosely based on the shapes used by ``content/search`` in
``openedx-platform`` and are test fixtures, not product code.
"""

from __future__ import annotations

from openedx_search.registry import UseCaseRegistration
from openedx_search.schema import Capabilities, Field, FieldType, IndexSchema, Searchable

__all__ = [
    "STUDIO_COURSE",
    "STUDIO_COURSE_SCHEMA",
    "STUDIO_LIBRARY",
    "STUDIO_LIBRARY_SCHEMA",
]


def _tag(name: str, searchable: Searchable) -> Field:
    """Build one hierarchical tag facet field."""
    return Field(
        name,
        FieldType.STRING,
        array=True,
        capabilities=Capabilities(
            searchable=searchable,
            filterable=True,
            facetable=True,
        ),
    )


STUDIO_COURSE_SCHEMA = IndexSchema(
    name="studio_course",
    version=1,
    fields=(
        Field("id", FieldType.STRING, required=True),
        Field("usage_key", FieldType.STRING, required=True, capabilities=Capabilities(filterable=True)),
        Field(
            "block_id",
            FieldType.STRING,
            required=True,
            capabilities=Capabilities(searchable=Searchable(order=2), filterable=True),
        ),
        Field(
            "block_type",
            FieldType.STRING,
            required=True,
            capabilities=Capabilities(filterable=True, facetable=True),
        ),
        Field(
            "display_name",
            FieldType.STRING,
            required=True,
            capabilities=Capabilities(searchable=Searchable(order=1), sortable=True),
        ),
        Field("description", FieldType.STRING, capabilities=Capabilities(searchable=Searchable(order=4))),
        Field("modified", FieldType.DATETIME, capabilities=Capabilities(sortable=True)),
        Field(
            "context_key",
            FieldType.STRING,
            required=True,
            capabilities=Capabilities(filterable=True, facetable=True),
        ),
        Field("org", FieldType.STRING, required=True, capabilities=Capabilities(filterable=True, facetable=True)),
        Field("access_id", FieldType.INT64, required=True, capabilities=Capabilities(filterable=True)),
        Field("breadcrumbs", FieldType.OBJECT, array=True, required=True),
        Field("breadcrumbs.display_name", FieldType.STRING),
        Field("breadcrumbs.usage_key", FieldType.STRING, capabilities=Capabilities(filterable=True)),
        Field(
            "tags",
            FieldType.OBJECT,
            capabilities=Capabilities(
                searchable=Searchable(order=5),
                filterable=True,
                facetable=True,
            ),
        ),
        _tag("tags.taxonomy", Searchable(order=6)),
        _tag("tags.level0", Searchable(order=7)),
        _tag("tags.level1", Searchable(order=8)),
        _tag("tags.level2", Searchable(order=9)),
        _tag("tags.level3", Searchable(order=10)),
        Field(
            "content",
            FieldType.OBJECT,
            additional_properties=True,
            capabilities=Capabilities(searchable=Searchable(order=3)),
        ),
        Field(
            "content.problem_types",
            FieldType.STRING,
            array=True,
            capabilities=Capabilities(filterable=True, facetable=True),
        ),
    ),
    sort_overrides_relevance=True,
)


# Library documents: ``library_block``, ``library_container`` and ``collection``.
# The schema is the union of the three shapes; each document populates a subset.
STUDIO_LIBRARY_SCHEMA = IndexSchema(
    name="studio_library",
    version=1,
    fields=(
        Field("id", FieldType.STRING, required=True),
        Field("usage_key", FieldType.STRING, required=True, capabilities=Capabilities(filterable=True)),
        Field(
            "type",
            FieldType.STRING,
            required=True,
            capabilities=Capabilities(filterable=True, facetable=True),
        ),
        Field(
            "block_id",
            FieldType.STRING,
            required=True,
            capabilities=Capabilities(searchable=Searchable(order=2), filterable=True),
        ),
        Field("block_type", FieldType.STRING, capabilities=Capabilities(filterable=True, facetable=True)),
        Field(
            "display_name",
            FieldType.STRING,
            required=True,
            capabilities=Capabilities(searchable=Searchable(order=1), sortable=True),
        ),
        Field("description", FieldType.STRING, capabilities=Capabilities(searchable=Searchable(order=4))),
        Field("created", FieldType.DATETIME, required=True, capabilities=Capabilities(sortable=True)),
        Field("modified", FieldType.DATETIME, required=True, capabilities=Capabilities(sortable=True)),
        Field(
            "last_published",
            FieldType.DATETIME,
            capabilities=Capabilities(filterable=True, sortable=True),
        ),
        Field(
            "context_key",
            FieldType.STRING,
            required=True,
            capabilities=Capabilities(filterable=True, facetable=True),
        ),
        Field("org", FieldType.STRING, required=True, capabilities=Capabilities(filterable=True, facetable=True)),
        Field("access_id", FieldType.INT64, required=True, capabilities=Capabilities(filterable=True)),
        Field("publish_status", FieldType.STRING, capabilities=Capabilities(filterable=True, facetable=True)),
        Field("num_children", FieldType.INT32),
        Field("breadcrumbs", FieldType.OBJECT, array=True, required=True),
        Field("breadcrumbs.display_name", FieldType.STRING),
        Field("breadcrumbs.usage_key", FieldType.STRING, capabilities=Capabilities(filterable=True)),
        Field(
            "tags",
            FieldType.OBJECT,
            capabilities=Capabilities(
                searchable=Searchable(order=5),
                filterable=True,
                facetable=True,
            ),
        ),
        _tag("tags.taxonomy", Searchable(order=7)),
        _tag("tags.level0", Searchable(order=8)),
        _tag("tags.level1", Searchable(order=9)),
        _tag("tags.level2", Searchable(order=10)),
        _tag("tags.level3", Searchable(order=11)),
        Field(
            "collections",
            FieldType.OBJECT,
            capabilities=Capabilities(
                searchable=Searchable(order=6),
                filterable=True,
                facetable=True,
            ),
        ),
        Field(
            "collections.display_name",
            FieldType.STRING,
            array=True,
            capabilities=Capabilities(
                searchable=Searchable(order=12),
                filterable=True,
                facetable=True,
            ),
        ),
        Field(
            "collections.key",
            FieldType.STRING,
            array=True,
            capabilities=Capabilities(
                searchable=Searchable(order=13),
                filterable=True,
                facetable=True,
            ),
        ),
        Field("units", FieldType.OBJECT),
        Field("units.display_name", FieldType.STRING, array=True),
        Field("units.key", FieldType.STRING, array=True),
        Field("subsections", FieldType.OBJECT),
        Field("subsections.display_name", FieldType.STRING, array=True),
        Field("subsections.key", FieldType.STRING, array=True),
        Field("sections", FieldType.OBJECT),
        Field("sections.display_name", FieldType.STRING, array=True),
        Field("sections.key", FieldType.STRING, array=True),
        Field(
            "content",
            FieldType.OBJECT,
            additional_properties=True,
            capabilities=Capabilities(searchable=Searchable(order=3)),
        ),
        Field(
            "content.problem_types",
            FieldType.STRING,
            array=True,
            capabilities=Capabilities(filterable=True, facetable=True),
        ),
        Field("content.child_usage_keys", FieldType.STRING, array=True),
        Field("content.child_display_names", FieldType.STRING, array=True),
        Field("published", FieldType.OBJECT),
        Field(
            "published.display_name",
            FieldType.STRING,
            capabilities=Capabilities(searchable=Searchable(order=14)),
        ),
        Field(
            "published.description",
            FieldType.STRING,
            capabilities=Capabilities(searchable=Searchable(order=15)),
        ),
        Field("published.num_children", FieldType.INT32),
        Field("published.content", FieldType.OBJECT, additional_properties=True),
        Field("published.content.child_usage_keys", FieldType.STRING, array=True),
        Field("published.content.child_display_names", FieldType.STRING, array=True),
    ),
    sort_overrides_relevance=True,
)

STUDIO_COURSE = UseCaseRegistration(
    use_case="studio_course",
    index="studio_content",
    schema=STUDIO_COURSE_SCHEMA,
)

STUDIO_LIBRARY = UseCaseRegistration(
    use_case="studio_library",
    index="studio_library_content",
    schema=STUDIO_LIBRARY_SCHEMA,
)
