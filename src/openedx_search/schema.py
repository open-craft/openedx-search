"""
Engine-neutral search schema model for openedx-search.

This module defines the declaration described in ADR 0002: one ``IndexSchema``
per use case. It is plain Python (no Django, no engine client), so it can be
imported from application code, management commands and tests.

Only the surface the current consumers use is modelled: document fields,
searchable order, filterable, facetable and sortable capabilities, ranking
rules and a distinct attribute. Engine-specific settings such as typo
tolerance, synonyms, tokenization, vectors and replicas are deliberately
absent; add them when a consumer needs them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "Capabilities",
    "Distinct",
    "Field",
    "FieldType",
    "IndexSchema",
    "Ranking",
    "RankingRule",
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


class RankingRule(StrEnum):
    """Engine-neutral ranking rules used by the current consumers."""

    TYPO = "typo"
    WORDS = "words"
    PROXIMITY = "proximity"
    ATTRIBUTE = "attribute"
    EXACTNESS = "exactness"
    SORT = "sort"


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
    whose keys are not declared individually.
    """

    name: str
    type: FieldType | str = FieldType.STRING
    array: bool = False
    additional_properties: bool = False
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
        if self.capabilities.sortable:
            _require(field_type is not FieldType.OBJECT, f"object field {self.name!r} cannot be sortable")


@dataclass(frozen=True)
class Ranking:
    """Ordered ranking rules."""

    rules: tuple[RankingRule | str, ...] = ()

    def __post_init__(self) -> None:
        """Normalize and validate the rule list."""
        rules = tuple(_enum(RankingRule, rule, "ranking rule") for rule in self.rules)
        _require(len(set(rules)) == len(rules), "ranking rules must be unique")
        object.__setattr__(self, "rules", rules)


@dataclass(frozen=True)
class Distinct:
    """Deduplicate hits by a field value."""

    attribute: str


@dataclass(frozen=True)
class IndexSchema:
    """
    A complete, engine-neutral index declaration.

    ``name`` identifies the schema, not necessarily the index it is mounted
    under: a use-case registration carries the logical index name, which lets
    two registrations share one schema.
    """

    name: str
    version: int = 1
    primary_key: str = "id"
    fields: tuple[Field, ...] = ()
    ranking: Ranking | None = None
    distinct: Distinct | None = None

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

        _require(
            self.primary_key in by_name,
            f"primary key field {self.primary_key!r} is not declared",
        )

        if self.distinct is not None:
            _require(
                self.distinct.attribute in by_name,
                f"distinct attribute {self.distinct.attribute!r} is not declared",
            )

    def field(self, name: str) -> Field:
        """
        Return the field with this exact name.

        Raises ``KeyError`` when the field is not declared.
        """
        for entry in self.fields:
            if entry.name == name:
                return entry
        raise KeyError(name)
