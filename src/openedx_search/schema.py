"""
Engine-neutral search schema model for openedx-search.

This module defines the declaration described in ADR 0002: one ``IndexSchema``
per use case. It is plain Python (no Django, no engine client), so it can be
imported from application code, management commands and tests.

Only the surface the current consumers use is modelled: document fields,
searchable order, filterable, facetable and sortable capabilities. Engine-specific
settings such as typo tolerance, synonyms, tokenization, vectors and replicas are
deliberately absent; add them when a consumer needs them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "Capabilities",
    "Field",
    "FieldType",
    "IndexSchema",
    "SchemaError",
    "Searchable",
]

INDEX_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
FIELD_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
USE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class SchemaError(ValueError):
    """A schema or one of its parts is invalid."""


class FieldType(StrEnum):
    """
    Document field types shared by the current consumers.

    ``UNKNOWN`` is for fields parsed from engines that do not store types,
    such as Meilisearch and Algolia.
    """

    STRING = "string"
    INT32 = "int32"
    INT64 = "int64"
    FLOAT = "float"
    BOOL = "bool"
    DATETIME = "datetime"
    GEOPOINT = "geopoint"
    OBJECT = "object"
    UNKNOWN = "unknown"


def _require(condition: bool, message: str) -> None:
    """Raise ``SchemaError`` when ``condition`` is false."""
    if not condition:
        raise SchemaError(message)


def _enum(enum_type, value, label: str):
    """Coerce ``value`` to ``enum_type``, raising ``SchemaError`` if invalid."""
    try:
        return enum_type(value)
    except ValueError as exc:
        raise SchemaError(f"invalid {label}: {value!r}") from exc


def _validate_field_name(name: str) -> None:
    """Reject field names that the engines cannot address safely."""
    _require(isinstance(name, str) and bool(name), "field name must be a non-empty string")
    _require(
        not name.startswith(".") and not name.endswith(".") and ".." not in name,
        f"invalid field path {name!r}",
    )
    _require(bool(FIELD_NAME_PATTERN.match(name)), f"invalid field name {name!r}")


@dataclass(frozen=True)
class Searchable:
    """
    Searchable configuration for a field.

    ``order`` is the field's position in the attribute ranking order: lower is
    more important. It is an index setting, not a query parameter.
    """

    order: int | None = None

    def __post_init__(self) -> None:
        """Validate the ranking position."""
        if self.order is not None:
            _require(self.order >= 1, "searchable order must be >= 1")


@dataclass(frozen=True)
class Capabilities:
    """
    Cross-engine capability flags for one field.

    ``facetable`` implies ``filterable`` in every supported engine, so setting
    it also sets ``filterable``.
    """

    searchable: bool | Searchable = False
    filterable: bool = False
    facetable: bool = False
    sortable: bool = False

    def __post_init__(self) -> None:
        """Validate the flag types and normalize facetability."""
        _require(
            isinstance(self.searchable, (bool, Searchable)),
            "searchable must be a bool or Searchable",
        )
        if self.facetable and not self.filterable:
            object.__setattr__(self, "filterable", True)


@dataclass(frozen=True)
class Field:
    """
    A document field.

    Nested fields use dot paths (``tags.level0``), which is how all supported
    engines address them. ``additional_properties`` marks a free-form object
    whose keys are not declared individually. ``required`` marks a field every
    document must provide; the Typesense renderer emits ``optional: false``.
    ``searchable`` is limited to string fields and objects; an array field
    cannot be sortable.
    """

    name: str
    type: FieldType | str = FieldType.STRING
    array: bool = False
    additional_properties: bool = False
    required: bool = False
    capabilities: Capabilities = field(default_factory=Capabilities)

    def __post_init__(self) -> None:
        """Validate and normalize the field."""
        field_type = _enum(FieldType, self.type, "field type")
        object.__setattr__(self, "type", field_type)

        _validate_field_name(self.name)
        _require(
            isinstance(self.capabilities, Capabilities),
            "capabilities must be a Capabilities instance",
        )
        if self.additional_properties:
            _require(field_type is FieldType.OBJECT, "additional_properties requires an object field")
        if self.capabilities.searchable:
            _require(
                field_type in (FieldType.STRING, FieldType.OBJECT),
                f"searchable field {self.name!r} must be a string or object",
            )
        if self.capabilities.sortable:
            _require(field_type is not FieldType.OBJECT, f"object field {self.name!r} cannot be sortable")
            _require(not self.array, f"array field {self.name!r} cannot be sortable")


def _validate_id(by_name: dict[str, Field]) -> None:
    """Require the fixed string id field."""
    id_field = by_name.get("id")
    _require(id_field is not None, "a schema must declare the required string field 'id'")
    _require(id_field.type is FieldType.STRING, "field 'id' must be a string")
    _require(id_field.required, "field 'id' must be required")
    _require(not id_field.array, "field 'id' cannot be an array")


def _validate_parent_paths(by_name: dict[str, Field]) -> None:
    """Require a declared object parent for every nested field path."""
    for name in by_name:
        if "." not in name:
            continue
        parent_name = name.rsplit(".", 1)[0]
        parent = by_name.get(parent_name)
        _require(parent is not None, f"field {name!r} has no declared parent {parent_name!r}")
        _require(parent.type is FieldType.OBJECT, f"parent field {parent_name!r} must be an object")


def _validate_searchable_objects(by_name: dict[str, Field]) -> None:
    """Reject searchable objects whose declared leaves are not strings."""
    for entry in by_name.values():
        if entry.type is not FieldType.OBJECT or not entry.capabilities.searchable:
            continue
        prefix = entry.name + "."
        for child_name, child in by_name.items():
            if not child_name.startswith(prefix):
                continue
            if any(other_name.startswith(child_name + ".") for other_name in by_name):
                continue
            _require(
                child.type is FieldType.STRING,
                f"searchable object {entry.name!r} has non-string leaf {child_name!r}",
            )


@dataclass(frozen=True)
class IndexSchema:
    """
    A complete, engine-neutral index declaration.

    ``name`` identifies the schema, not necessarily the index it is mounted
    under: a use-case registration carries the logical index name, which lets
    two registrations share one schema. The primary key is the required string
    field ``id``.
    """

    name: str
    version: int = 1
    fields: tuple[Field, ...] = ()

    def __post_init__(self) -> None:
        """Validate cross-field invariants and freeze the field tuple."""
        _require(bool(INDEX_NAME_PATTERN.match(self.name)), f"invalid schema name {self.name!r}")
        _require(self.version >= 1, "schema version must be >= 1")

        fields = tuple(self.fields)
        _require(bool(fields), "a schema needs at least one field")
        object.__setattr__(self, "fields", fields)

        by_name: dict[str, Field] = {}
        for entry in fields:
            _require(entry.name not in by_name, f"duplicate field name {entry.name!r}")
            by_name[entry.name] = entry

        _validate_id(by_name)
        _validate_parent_paths(by_name)
        _validate_searchable_objects(by_name)

    def field(self, name: str) -> Field:
        """
        Return the field with this exact name.

        Raises ``KeyError`` when the field is not declared.
        """
        for entry in self.fields:
            if entry.name == name:
                return entry
        raise KeyError(name)
