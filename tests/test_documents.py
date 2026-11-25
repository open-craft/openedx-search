"""Tests for document validation against a schema."""

from datetime import datetime, timezone
from typing import Any

import pytest

from openedx_search.documents import DocumentError, validate_document, validate_documents
from openedx_search.schema import MAX_DOCUMENT_ID_BYTES, Field, FieldType, IndexSchema

SCHEMA = IndexSchema(
    name="documents",
    fields=(
        Field("id", FieldType.STRING, required=True),
        Field("title", FieldType.STRING, required=True),
        Field("count", FieldType.INT32),
        Field("rating", FieldType.FLOAT),
        Field("active", FieldType.BOOL),
        Field("created", FieldType.DATETIME, required=True),
        Field("tags", FieldType.STRING, array=True),
        Field("details", FieldType.OBJECT, required=True),
        Field("details.name", FieldType.STRING, required=True),
        Field("breadcrumbs", FieldType.OBJECT, array=True, required=True),
        Field("breadcrumbs.label", FieldType.STRING, array=True),
        Field("content", FieldType.OBJECT, additional_properties=True),
        Field("content.problem_types", FieldType.STRING, array=True),
    ),
)


def _document(**overrides: Any) -> dict[str, Any]:
    """Build a valid document, with overrides for one test."""
    document = {
        "id": "doc-1",
        "title": "Hello",
        "count": 2,
        "rating": 4.5,
        "active": True,
        "created": datetime(2023, 4, 5, 6, 7, 8, tzinfo=timezone.utc),
        "tags": ["a", "b"],
        "details": {"name": "details"},
        "breadcrumbs": [{"label": "one"}, {"label": "two"}],
        "content": {"problem_types": ["multiple_choice"], "html": "text"},
    }
    document.update(overrides)
    return document


def test_valid_document_passes():
    """Every declared type, array, object and free-form value is accepted."""
    validate_document(SCHEMA, _document())


def test_a_document_must_be_a_mapping():
    """A string is not a document."""
    with pytest.raises(DocumentError, match="mapping"):
        validate_document(SCHEMA, "not a mapping")


def test_error_names_the_document_once():
    """The label is applied at the validation boundary, not at every raise site."""
    document = _document()
    document.pop("title")

    with pytest.raises(DocumentError, match=r"^document 'doc-1': required field 'title' is missing$"):
        validate_document(SCHEMA, document)


def test_error_without_an_id_uses_a_generic_label():
    """A document with no usable id is still named in the error."""
    with pytest.raises(DocumentError, match=r"^document: required field 'id' is missing$"):
        validate_document(SCHEMA, {})


def test_missing_required_nested_field():
    """A required leaf inside a required object is checked."""
    with pytest.raises(DocumentError, match="required field 'details.name'"):
        validate_document(SCHEMA, _document(details={}))


def test_missing_optional_fields_pass():
    """Optional fields can be omitted."""
    document = _document()
    for key in ("count", "rating", "active", "tags", "content"):
        document.pop(key)
    validate_document(SCHEMA, document)


def test_null_is_rejected():
    """Null is not a value, even for an optional field."""
    with pytest.raises(DocumentError, match="cannot be null"):
        validate_document(SCHEMA, _document(count=None))


def test_null_array_element_is_rejected():
    """Null inside an array is rejected by the element check."""
    with pytest.raises(DocumentError, match="field 'tags' cannot be null"):
        validate_document(SCHEMA, _document(tags=["a", None]))


def test_unknown_field_is_rejected():
    """Undeclared fields are errors, not silently dropped."""
    with pytest.raises(DocumentError, match="unknown field 'extra'"):
        validate_document(SCHEMA, _document(extra="value"))


def test_field_names_must_be_strings():
    """A non-string key cannot be addressed by the engines."""
    document = _document()
    document[1] = "value"

    with pytest.raises(DocumentError, match="field names must be strings"):
        validate_document(SCHEMA, document)


def test_unknown_nested_field_is_rejected():
    """Undeclared fields inside a declared object are errors."""
    with pytest.raises(DocumentError, match="unknown field 'details.extra'"):
        validate_document(SCHEMA, _document(details={"name": "x", "extra": "y"}))


def test_dotted_document_key_is_rejected():
    """A flat key with a dot is not a nested document field."""
    with pytest.raises(DocumentError, match="unknown field 'content.problem_types'"):
        validate_document(
            SCHEMA,
            _document(**{"content.problem_types": ["multiple_choice"]}),
        )


def test_wrong_scalar_type():
    """Declared types are enforced."""
    with pytest.raises(DocumentError, match="must be a string"):
        validate_document(SCHEMA, _document(title=1))


def test_boolean_is_not_an_integer():
    """A bool is not a valid int32."""
    with pytest.raises(DocumentError, match="must be an integer"):
        validate_document(SCHEMA, _document(count=True))


def test_float_field_rejects_a_string():
    """The float type name appears in the error."""
    with pytest.raises(DocumentError, match="field 'rating' must be a number"):
        validate_document(SCHEMA, _document(rating="high"))


def test_bool_field_rejects_a_string():
    """The bool type name appears in the error."""
    with pytest.raises(DocumentError, match="field 'active' must be a boolean"):
        validate_document(SCHEMA, _document(active="yes"))


def test_object_field_must_be_a_mapping():
    """A declared object is not a string or an array."""
    with pytest.raises(DocumentError, match="field 'details' must be an object"):
        validate_document(SCHEMA, _document(details="not an object"))


def test_naive_datetime_is_rejected():
    """A naive datetime depends on the local timezone."""
    with pytest.raises(DocumentError, match="timezone-aware"):
        validate_document(SCHEMA, _document(created=datetime(2023, 4, 5, 6, 7, 8)))


def test_numeric_datetime_is_accepted():
    """POSIX seconds remain valid, which is the stored form."""
    validate_document(SCHEMA, _document(created=1680674828.0))


def test_array_field_requires_an_array():
    """A bare string is not an array of strings."""
    with pytest.raises(DocumentError, match="must be an array"):
        validate_document(SCHEMA, _document(tags="a"))


def test_array_elements_are_typed():
    """Each element is validated."""
    with pytest.raises(DocumentError, match="must be a string"):
        validate_document(SCHEMA, _document(tags=["a", 2]))


def test_object_array_elements_are_objects():
    """An object array holds mappings."""
    with pytest.raises(DocumentError, match="must contain objects"):
        validate_document(SCHEMA, _document(breadcrumbs=["one"]))


def test_object_array_inside_an_object_array():
    """An object array nested in an object array is iterated, not a single object."""
    schema = IndexSchema(
        name="nested_arrays",
        fields=(
            Field("id", FieldType.STRING, required=True),
            Field("a", FieldType.OBJECT, array=True, required=True),
            Field("a.b", FieldType.OBJECT, array=True),
            Field("a.b.c", FieldType.STRING, array=True),
        ),
    )

    validate_document(schema, {"id": "x", "a": [{"b": [{"c": "v"}]}]})


def test_object_array_children_are_scalars_per_element():
    """The array flag on a child is implied by the parent array, not the value."""
    with pytest.raises(DocumentError, match="must be a string"):
        validate_document(SCHEMA, _document(breadcrumbs=[{"label": ["one"]}]))


def test_free_form_values_must_be_strings():
    """Free-form object values are string-only."""
    with pytest.raises(DocumentError, match="must be a string"):
        validate_document(SCHEMA, _document(content={"html": 5}))


def test_declared_child_of_free_form_object_is_typed():
    """A declared leaf under a free-form object is validated normally."""
    with pytest.raises(DocumentError, match="must be an array"):
        validate_document(SCHEMA, _document(content={"problem_types": "multiple_choice"}))


def test_invalid_document_id_is_rejected():
    """The id is engine-safe."""
    with pytest.raises(DocumentError, match=r"^document 'a:b': invalid document id"):
        validate_document(SCHEMA, _document(id="a:b"))


def test_too_long_document_id_is_rejected():
    """An id above the filterable limit would fail only at the engine."""
    with pytest.raises(DocumentError, match="maximum is 468"):
        validate_document(SCHEMA, _document(id="a" * (MAX_DOCUMENT_ID_BYTES + 1)))


def test_validate_documents_checks_every_document():
    """One bad document fails the batch."""
    with pytest.raises(DocumentError, match="must be a string"):
        validate_documents(SCHEMA, [_document(), _document(title=1)])
