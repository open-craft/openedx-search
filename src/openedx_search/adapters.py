"""
Engine adapter contract for openedx-search.

This module is the executable form of ADR 0002's adapter contract. An adapter
implements one engine; the library will resolve it from ``BackendConfig.engine``
when the engine adapters land and delegate the operations below. The abstract
class exists so an engine contribution has one place to see the full surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias

from .config import BackendConfig
from .registry import UseCaseRegistration
from .schema import FIELD_NAME_PATTERN, IndexSchema

__all__ = [
    "Adapter",
    "AdapterError",
    "Document",
    "DocumentBatch",
    "FilterExpression",
    "FilterTerm",
    "PrimaryKey",
    "RebuildTarget",
    "ReconcileResult",
    "SearchResult",
    "UnsupportedSchema",
    "WriteFailed",
    "WriteReceipt",
]

Document: TypeAlias = Mapping[str, Any]
DocumentBatch: TypeAlias = Sequence[Document]
PrimaryKey: TypeAlias = str | int


class AdapterError(RuntimeError):
    """An adapter could not complete an operation."""


class UnsupportedSchema(AdapterError):
    """The adapter cannot represent part of the declared schema."""


class WriteFailed(AdapterError):
    """The engine reported a failed write."""


@dataclass(frozen=True)
class FilterTerm:
    """One exact-match filter clause; multiple terms are combined with AND."""

    field: str
    values: tuple[str | int, ...]

    def __post_init__(self) -> None:
        """Validate the field path and the filter values."""
        if not isinstance(self.field, str) or not FIELD_NAME_PATTERN.match(self.field):
            raise ValueError(f"invalid filter field {self.field!r}")
        values = tuple(self.values)
        if not values:
            raise ValueError("a filter term needs at least one value")
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (str, int)):
                raise ValueError(f"invalid filter value {value!r}")
            if isinstance(value, str) and not value:
                raise ValueError("filter values must not be empty")
        object.__setattr__(self, "values", values)


FilterExpression: TypeAlias = tuple[FilterTerm, ...]


@dataclass(frozen=True)
class WriteReceipt:
    """Submission acknowledgment; completion is distinct from submission."""

    task_id: int | None = None
    complete: bool = False


@dataclass(frozen=True)
class RebuildTarget:
    """A rebuild target created by ``begin_rebuild``."""

    index: str
    version: int


@dataclass(frozen=True)
class ReconcileResult:
    """What ``reconcile`` changed for one index."""

    index: str
    version: int
    changed: bool = False


@dataclass(frozen=True)
class SearchResult:
    """Documents and count accuracy from one search."""

    documents: tuple[Document, ...]
    total: int
    total_is_exact: bool = False


class Adapter(ABC):
    """
    One engine implementation for a registered use case.

    A factory receives the registration and the resolved backend, so the
    adapter knows its logical index, schema and connection options.
    """

    def __init__(self, registration: UseCaseRegistration, backend: BackendConfig) -> None:
        """Store the registration and backend configuration."""
        self.registration = registration
        self.backend = backend

    @property
    def index(self) -> str:
        """Return the logical index name."""
        return self.registration.index

    @property
    def schema(self) -> IndexSchema:
        """Return the declared schema."""
        return self.registration.schema

    @abstractmethod
    def validate_schema(self, schema: IndexSchema) -> None:
        """Raise ``UnsupportedSchema`` when the adapter cannot represent ``schema``."""

    @abstractmethod
    def configure(self, schema: IndexSchema) -> None:
        """Render and apply the index settings for ``schema``."""

    @abstractmethod
    def reconcile(self, schema: IndexSchema) -> ReconcileResult:
        """Create the index if needed and apply drift-free settings."""

    @abstractmethod
    def begin_rebuild(self, schema: IndexSchema) -> RebuildTarget:
        """Create a rebuild target for ``schema`` and return its handle."""

    @abstractmethod
    def commit_rebuild(self, target: RebuildTarget) -> None:
        """Wait for the target's writes, swap it in and delete the old target."""

    @abstractmethod
    def abort_rebuild(self, target: RebuildTarget) -> None:
        """Discard ``target`` after a failed rebuild."""

    @abstractmethod
    def upsert(self, documents: Iterable[Document], target: RebuildTarget | None = None) -> WriteReceipt:
        """Submit one bounded batch of documents for upsert."""

    @abstractmethod
    def delete(
        self,
        primary_keys: Sequence[PrimaryKey] | None = None,
        *,
        filter: FilterExpression | None = None,  # pylint: disable=redefined-builtin
        target: RebuildTarget | None = None,
    ) -> WriteReceipt:
        """Submit a delete by primary key or by the abstract filter expression."""

    @abstractmethod
    def check_write(self, receipt: WriteReceipt) -> WriteReceipt:
        """Perform one status check; never sleep or retry."""

    @abstractmethod
    def search(self, query: Any) -> SearchResult:
        """Run one search. The query model is specified separately."""

    @abstractmethod
    def mint_credential(self, scope: Mapping[str, Any], expires: int) -> str:
        """Mint a scoped, expiring credential."""
