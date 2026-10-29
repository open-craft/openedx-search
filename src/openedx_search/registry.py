"""
Use-case registration API.

ADR 0002, Decision 1: every search use case registers itself, declaring its
logical index and schema. The registration is engine-neutral; the deployment
chooses the backend.

The registry is process-local and thread-safe. Registrations normally happen at
import time, and ``replace=True`` is available for development reloads and
tests.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .schema import INDEX_NAME_PATTERN, USE_CASE_PATTERN, IndexSchema

__all__ = [
    "RegistrationError",
    "UseCaseAlreadyRegistered",
    "UseCaseNotRegistered",
    "UseCaseRegistration",
    "clear_registry",
    "get_registration",
    "iter_registrations",
    "register_use_case",
    "unregister_use_case",
]


class RegistrationError(ValueError):
    """A use-case registration is invalid."""


class UseCaseAlreadyRegistered(RegistrationError):
    """The use case already has a registration."""


class UseCaseNotRegistered(LookupError):
    """There is no registration for the requested use case."""


def _require(condition: bool, message: str) -> None:
    """Raise ``RegistrationError`` when ``condition`` is false."""
    if not condition:
        raise RegistrationError(message)


@dataclass(frozen=True)
class UseCaseRegistration:
    """
    One search surface registered with the library.

    ``index`` is a logical name. A use case owns it; another use case cannot
    register the same index. A backend maps it to its own namespace, such as a
    Meilisearch index UID or a Typesense collection plus its alias.
    """

    use_case: str
    index: str
    schema: IndexSchema

    def __post_init__(self) -> None:
        """Validate the registration."""
        _require(
            isinstance(self.use_case, str) and bool(USE_CASE_PATTERN.match(self.use_case)),
            f"invalid use case name {self.use_case!r}",
        )
        _require(
            isinstance(self.index, str) and bool(INDEX_NAME_PATTERN.match(self.index)),
            f"invalid index name {self.index!r}",
        )
        _require(isinstance(self.schema, IndexSchema), "schema must be an IndexSchema")


_lock = threading.RLock()
_registrations: dict[str, UseCaseRegistration] = {}


def register_use_case(
    registration: UseCaseRegistration,
    *,
    replace: bool = False,
) -> UseCaseRegistration:
    """
    Register a use case and return it.

    Raises ``UseCaseAlreadyRegistered`` when the use case is already present,
    unless ``replace`` is true. Raises ``RegistrationError`` when the index is
    already owned by a different use case.
    """
    _require(isinstance(registration, UseCaseRegistration), "registration must be a UseCaseRegistration")
    with _lock:
        if registration.use_case in _registrations and not replace:
            raise UseCaseAlreadyRegistered(f"use case {registration.use_case!r} is already registered")
        for existing in _registrations.values():
            if existing.use_case == registration.use_case:
                continue
            if existing.index == registration.index:
                raise RegistrationError(
                    f"index {registration.index!r} is already used by use case {existing.use_case!r}"
                )
        _registrations[registration.use_case] = registration
    return registration


def get_registration(use_case: str) -> UseCaseRegistration:
    """
    Return the registration for ``use_case``.

    Raises ``UseCaseNotRegistered`` when there is none.
    """
    with _lock:
        try:
            return _registrations[use_case]
        except KeyError:
            raise UseCaseNotRegistered(f"no registration for use case {use_case!r}") from None


def iter_registrations() -> tuple[UseCaseRegistration, ...]:
    """Return every registration, ordered by use case name."""
    with _lock:
        return tuple(_registrations[name] for name in sorted(_registrations))


def unregister_use_case(use_case: str) -> None:
    """
    Remove a registration.

    Raises ``UseCaseNotRegistered`` when there is none.
    """
    with _lock:
        try:
            del _registrations[use_case]
        except KeyError:
            raise UseCaseNotRegistered(f"no registration for use case {use_case!r}") from None


def clear_registry() -> None:
    """Remove every registration. Intended for tests and process shutdown."""
    with _lock:
        _registrations.clear()
