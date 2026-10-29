"""Tests for the use-case registration API."""

import pytest

from openedx_search.registry import (
    RegistrationError,
    UseCaseAlreadyRegistered,
    UseCaseNotRegistered,
    UseCaseRegistration,
    get_registration,
    iter_registrations,
    register_use_case,
    unregister_use_case,
)
from tests.fixtures import STUDIO_COURSE, STUDIO_COURSE_SCHEMA, STUDIO_LIBRARY, STUDIO_LIBRARY_SCHEMA


def test_register_and_get():
    """A registration is retrievable by its use case."""
    register_use_case(STUDIO_COURSE)
    assert get_registration("studio_course") is STUDIO_COURSE


def test_duplicate_registration_is_rejected():
    """Registering the same use case twice fails by default."""
    register_use_case(STUDIO_COURSE)
    with pytest.raises(UseCaseAlreadyRegistered):
        register_use_case(STUDIO_COURSE)


def test_duplicate_index_is_rejected():
    """A logical index belongs to one use case."""
    register_use_case(STUDIO_COURSE)
    with pytest.raises(RegistrationError, match="index 'studio_content' is already used"):
        register_use_case(
            UseCaseRegistration(
                use_case="other_course",
                index="studio_content",
                schema=STUDIO_COURSE_SCHEMA,
            )
        )


def test_replace_cannot_take_another_use_case_index():
    """``replace=True`` cannot take an index owned by another use case."""
    register_use_case(STUDIO_COURSE)
    register_use_case(STUDIO_LIBRARY)
    replacement = UseCaseRegistration(
        use_case="studio_library",
        index="studio_content",
        schema=STUDIO_LIBRARY_SCHEMA,
    )
    with pytest.raises(RegistrationError, match="already used by use case 'studio_course'"):
        register_use_case(replacement, replace=True)


def test_unregister_frees_index():
    """Removing a registration makes its index available again."""
    register_use_case(STUDIO_COURSE)
    unregister_use_case("studio_course")
    register_use_case(
        UseCaseRegistration(
            use_case="other_course",
            index="studio_content",
            schema=STUDIO_COURSE_SCHEMA,
        )
    )
    assert get_registration("other_course").index == "studio_content"


def test_replace_registration():
    """``replace=True`` allows a controlled re-registration."""
    register_use_case(STUDIO_COURSE)
    replacement = UseCaseRegistration(
        use_case="studio_course",
        index="studio_content_v2",
        schema=STUDIO_COURSE_SCHEMA,
    )
    register_use_case(replacement, replace=True)
    assert get_registration("studio_course") is replacement


def test_iter_registrations_is_sorted():
    """Registrations come back ordered by use case name."""
    register_use_case(STUDIO_LIBRARY)
    register_use_case(STUDIO_COURSE)
    assert [item.use_case for item in iter_registrations()] == ["studio_course", "studio_library"]


def test_course_and_library_use_separate_schemas():
    """The two Studio indexes no longer share a document schema."""
    register_use_case(STUDIO_COURSE)
    register_use_case(STUDIO_LIBRARY)

    course = get_registration("studio_course")
    library = get_registration("studio_library")

    assert course.schema is STUDIO_COURSE_SCHEMA
    assert library.schema is STUDIO_LIBRARY_SCHEMA
    assert course.schema is not library.schema


def test_unregister_use_case():
    """A registration can be removed."""
    register_use_case(STUDIO_COURSE)
    unregister_use_case("studio_course")
    with pytest.raises(UseCaseNotRegistered):
        get_registration("studio_course")


def test_unknown_use_case_raises():
    """Looking up an unregistered use case is an error."""
    with pytest.raises(UseCaseNotRegistered):
        get_registration("missing")


def test_ready_style_registration_is_idempotent_with_replace():
    """A consumer's ``ready()`` may run more than once in tests or reloads."""
    register_use_case(STUDIO_COURSE, replace=True)
    register_use_case(STUDIO_COURSE, replace=True)
    assert get_registration("studio_course") is STUDIO_COURSE


def test_invalid_use_case_name():
    """Use case names are lower-case identifiers."""
    with pytest.raises(RegistrationError, match="invalid use case name"):
        UseCaseRegistration(
            use_case="Studio",
            index="studio_content",
            schema=STUDIO_COURSE_SCHEMA,
        )


def test_invalid_index_name():
    """Index names are restricted to the engine-safe charset."""
    with pytest.raises(RegistrationError, match="invalid index name"):
        UseCaseRegistration(
            use_case="studio_course",
            index="studio content",
            schema=STUDIO_COURSE_SCHEMA,
        )


def test_schema_is_required():
    """A registration cannot be built without an IndexSchema."""
    with pytest.raises(RegistrationError, match="schema must be an IndexSchema"):
        UseCaseRegistration(
            use_case="studio_course",
            index="studio_content",
            schema={},
        )
