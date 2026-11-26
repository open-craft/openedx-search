"""
Filter validation against a use case's schema.

A consumer passes an abstract filter expression; this module rejects the terms
the registration's :class:`IndexSchema` cannot filter, before anything reaches
an adapter.
"""

from __future__ import annotations

from .adapters import FilterExpression
from .schema import IndexSchema

__all__ = ["validate_filter_fields"]


def validate_filter_fields(schema: IndexSchema, expression: FilterExpression) -> None:
    """
    Reject filter terms the schema cannot filter.

    ``id`` is always filterable because the adapters make it so. Every other
    field must be declared and carry the ``filterable`` capability; a nested
    field is checked by its declared name, such as ``tags.level0``. An empty
    expression validates.
    """
    for group in expression:
        for term in group.terms:
            if term.field == "id":
                continue
            try:
                field = schema.field(term.field)
            except KeyError:
                raise ValueError(
                    f"filter field {term.field!r} is not declared in schema {schema.name!r}"
                ) from None
            if not field.capabilities.filterable:
                raise ValueError(f"filter field {term.field!r} is not declared filterable")
