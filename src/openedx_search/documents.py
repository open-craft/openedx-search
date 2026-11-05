"""
Document validation against an ``IndexSchema``.

ADR 0002: the library validates every document before it reaches an adapter, so
both engines accept and reject the same documents. A violation raises
``DocumentError`` naming the document id, the field and the reason.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .schema import Field, FieldType, IndexSchema, validate_document_id

__all__ = ["DocumentError", "validate_document", "validate_documents"]


class DocumentError(ValueError):
    """A document does not match its schema."""


def validate_documents(schema: IndexSchema, documents: Sequence[Mapping[str, Any]]) -> None:
    """Validate every document in one bounded batch."""
    for document in documents:
        validate_document(schema, document)


def validate_document(schema: IndexSchema, document: Mapping[str, Any]) -> None:
    """Validate one document against ``schema``."""
    if not isinstance(document, Mapping):
        raise DocumentError("a document must be a mapping")
    by_name = {field.name: field for field in schema.fields}
    _validate_object(by_name, document, prefix="", label=_label(document))


def _label(document: Mapping[str, Any]) -> str:
    """Name the document in an error, when it has an id."""
    doc_id = document.get("id")
    if isinstance(doc_id, str) and doc_id:
        return f"document {doc_id!r}"
    return "document"


def _validate_object(
    by_name: dict[str, Field],
    obj: Mapping[str, Any],
    *,
    prefix: str,
    label: str,
) -> None:
    """Validate one object level, including required children and free-form keys."""
    parent_name = prefix[:-1] if prefix else None
    parent = by_name.get(parent_name) if parent_name else None
    free_form = bool(parent and parent.additional_properties)
    inside_array = bool(parent and parent.type is FieldType.OBJECT and parent.array)

    for key, value in obj.items():
        if not isinstance(key, str):
            raise DocumentError(f"{label}: field names must be strings")
        full_name = f"{prefix}{key}"
        if "." in key and not free_form:
            raise DocumentError(f"{label}: unknown field {full_name!r}")
        field = by_name.get(full_name)
        if field is None:
            if free_form:
                if not isinstance(value, str):
                    raise DocumentError(f"{label}: free-form field {full_name!r} must be a string")
                continue
            raise DocumentError(f"{label}: unknown field {full_name!r}")
        _validate_field(by_name, field, value, inside_array=inside_array, label=label)

    _validate_required(by_name, obj, prefix=prefix, parent_name=parent_name, label=label)
    if prefix == "":
        try:
            validate_document_id(obj.get("id"))
        except ValueError as exc:
            raise DocumentError(f"{label}: {exc}") from exc


def _validate_required(
    by_name: dict[str, Field],
    obj: Mapping[str, Any],
    *,
    prefix: str,
    parent_name: str | None,
    label: str,
) -> None:
    """Require the fields declared required at this object level."""
    for name, field in by_name.items():
        if not field.required:
            continue
        field_parent = name.rsplit(".", 1)[0] if "." in name else None
        if field_parent != parent_name:
            continue
        if name[len(prefix):] not in obj:
            raise DocumentError(f"{label}: required field {name!r} is missing")


def _validate_field(
    by_name: dict[str, Field],
    field: Field,
    value: Any,
    *,
    inside_array: bool,
    label: str,
) -> None:
    """Validate one field value; array-ness is implied inside an object array."""
    if value is None:
        raise DocumentError(f"{label}: field {field.name!r} cannot be null")
    if field.type is FieldType.OBJECT:
        _validate_object_value(by_name, field, value, inside_array=inside_array, label=label)
        return
    if field.array and not inside_array:
        for element in _as_array(field, value, label):
            _validate_scalar(field, element, label)
        return
    _validate_scalar(field, value, label)


def _validate_object_value(
    by_name: dict[str, Field],
    field: Field,
    value: Any,
    *,
    inside_array: bool,
    label: str,
) -> None:
    """Validate an object, or an object array, and descend into its children."""
    if field.array and not inside_array:
        for element in _as_array(field, value, label):
            if not isinstance(element, Mapping):
                raise DocumentError(f"{label}: field {field.name!r} must contain objects")
            _validate_object(by_name, element, prefix=f"{field.name}.", label=label)
        return
    if not isinstance(value, Mapping):
        raise DocumentError(f"{label}: field {field.name!r} must be an object")
    _validate_object(by_name, value, prefix=f"{field.name}.", label=label)


def _as_array(field: Field, value: Any, label: str) -> Sequence[Any]:
    """Require a list or tuple, not a string or bytes."""
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise DocumentError(f"{label}: field {field.name!r} must be an array")
    return value


def _validate_scalar(field: Field, value: Any, label: str) -> None:
    """Validate one value against the field's base type."""
    if value is None:
        raise DocumentError(f"{label}: field {field.name!r} cannot be null")
    if not _matches_type(field.type, value):
        raise DocumentError(
            f"{label}: field {field.name!r} must be {_type_name(field.type)}"
        )


def _matches_type(field_type: FieldType, value: Any) -> bool:
    """Return whether a scalar value matches a field type."""
    if field_type is FieldType.STRING:
        return isinstance(value, str)
    if field_type in (FieldType.INT32, FieldType.INT64):
        return isinstance(value, int) and not isinstance(value, bool)
    if field_type is FieldType.FLOAT:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if field_type is FieldType.BOOL:
        return isinstance(value, bool)
    if field_type is FieldType.DATETIME:
        if isinstance(value, datetime):
            return value.tzinfo is not None and value.utcoffset() is not None
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


def _type_name(field_type: FieldType) -> str:
    """Describe a field type in an error message."""
    if field_type is FieldType.DATETIME:
        return "a timezone-aware datetime or POSIX seconds"
    if field_type is FieldType.FLOAT:
        return "a number"
    if field_type in (FieldType.INT32, FieldType.INT64):
        return "an integer"
    if field_type is FieldType.BOOL:
        return "a boolean"
    return f"a {field_type.value}"
