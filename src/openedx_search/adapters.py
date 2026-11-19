"""
Engine adapter contract for openedx-search.

This module is the executable form of ADR 0002's adapter contract. An adapter
implements one engine; the library will resolve it from ``BackendConfig.engine``
when the engine adapters land and delegate the operations below. The abstract
class exists so an engine contribution has one place to see the full surface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, TypeAlias

from .config import BackendConfig
from .registry import UseCaseRegistration
from .schema import FIELD_NAME_PATTERN, INDEX_NAME_PATTERN, IndexSchema

__all__ = [
    "Adapter",
    "AdapterError",
    "CredentialScope",
    "Document",
    "DocumentBatch",
    "FilterExpression",
    "FilterGroup",
    "FilterTerm",
    "FilterValue",
    "PrimaryKey",
    "RebuildTarget",
    "ReconcileResult",
    "SearchCredential",
    "SearchResult",
    "UnsupportedSchema",
    "WriteFailed",
    "WriteReceipt",
]

Document: TypeAlias = Mapping[str, Any]
DocumentBatch: TypeAlias = Sequence[Document]
PrimaryKey: TypeAlias = str
FilterValue: TypeAlias = str | int | float | bool | datetime


class AdapterError(RuntimeError):
    """An adapter could not complete an operation."""


class UnsupportedSchema(AdapterError):
    """The adapter cannot represent part of the declared schema."""


class WriteFailed(AdapterError):
    """The engine reported a failed write."""


@dataclass(frozen=True)
class FilterTerm:
    """One exact-match filter clause."""

    field: str
    values: tuple[FilterValue, ...]

    def __post_init__(self) -> None:
        """Validate the field path and the filter values."""
        if not isinstance(self.field, str) or not FIELD_NAME_PATTERN.match(self.field):
            raise ValueError(f"invalid filter field {self.field!r}")
        if isinstance(self.values, (str, bytes)):
            raise ValueError("filter values must be a sequence, not a string")
        if isinstance(self.values, Mapping):
            raise ValueError("filter values must be a sequence, not a mapping")
        try:
            values = tuple(self.values)
        except TypeError as exc:
            raise ValueError("filter values must be a sequence") from exc
        if not values:
            raise ValueError("a filter term needs at least one value")

        for value in values:
            if isinstance(value, datetime):
                if value.tzinfo is None or value.utcoffset() is None:
                    raise ValueError("datetime filter values must be timezone-aware")
            elif isinstance(value, str):
                if not value:
                    raise ValueError("filter values must not be empty")
            elif not isinstance(value, (bool, int, float)):
                raise ValueError(f"invalid filter value {value!r}")
        object.__setattr__(self, "values", values)


@dataclass(frozen=True)
class FilterGroup:
    """Terms combined with OR; groups are combined with AND."""

    terms: tuple[FilterTerm, ...]

    def __post_init__(self) -> None:
        """Validate and freeze the group."""
        if isinstance(self.terms, (str, bytes)):
            raise ValueError("filter group terms must be a sequence, not a string")
        terms = tuple(self.terms)
        if not terms:
            raise ValueError("a filter group needs at least one term")
        for term in terms:
            if not isinstance(term, FilterTerm):
                raise ValueError("filter group terms must be FilterTerm instances")
        object.__setattr__(self, "terms", terms)


FilterExpression: TypeAlias = tuple[FilterGroup, ...]


@dataclass(frozen=True)
class CredentialScope:
    """Tenant filters for a scoped frontend credential."""

    filters: FilterExpression = ()

    def __post_init__(self) -> None:
        """Validate and freeze the filter groups."""
        if isinstance(self.filters, (str, bytes)):
            raise ValueError("filters must be a sequence of FilterGroup, not a string")
        filters = tuple(self.filters)
        for group in filters:
            if not isinstance(group, FilterGroup):
                raise ValueError("filters must contain FilterGroup instances")
        object.__setattr__(self, "filters", filters)


@dataclass(frozen=True)
class WriteReceipt:
    """Submission acknowledgment; completion is distinct from submission."""

    task_id: int | None = None
    complete: bool = False


@dataclass(frozen=True)
class SearchCredential:
    """
    What the browser needs to query the engine directly.

    ``url`` is the public engine URL (``BackendConfig.public_url``), which can
    differ from the backend's connection URL. ``index_name`` is the physical name
    the adapter created, not the logical index of the registration, so consumers
    never derive it. ``api_key`` is the scoped key.
    """

    url: str
    index_name: str
    api_key: str

    def __post_init__(self) -> None:
        """Require all three fields."""
        for name in ("url", "index_name", "api_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class RebuildTarget:
    """
    A rebuild target minted by the library for ``begin_rebuild``.

    ``generation`` is unique to one rebuild and is not the schema version, so a
    second rebuild at the same version never derives the live index's name. That
    matters on Typesense, where the alias points at a collection named for the
    index and version and a collection cannot be renamed.
    """

    index: str
    version: int
    generation: str

    def __post_init__(self) -> None:
        """Validate the parts the engines require in a collection or index name."""
        if not isinstance(self.index, str) or not INDEX_NAME_PATTERN.match(self.index):
            raise ValueError(f"invalid rebuild index {self.index!r}")
        if not isinstance(self.version, int) or isinstance(self.version, bool) or self.version < 1:
            raise ValueError("rebuild version must be an integer >= 1")
        if not isinstance(self.generation, str) or not INDEX_NAME_PATTERN.match(self.generation):
            raise ValueError(f"invalid rebuild generation {self.generation!r}")

    @property
    def name(self) -> str:
        """Return the engine-visible target name."""
        return f"{self.index}_v{self.version}_{self.generation}"


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
    adapter knows its logical index, schema and connection options. Lifecycle
    methods use ``self.schema``; the registration is the single source of the
    declared schema.
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
    def validate_schema(self) -> None:
        """
        Raise ``UnsupportedSchema`` when the adapter cannot represent ``self.schema``.

        The Django system check calls this for each registration; registration
        itself never resolves an adapter.
        """

    @abstractmethod
    def reconcile(self) -> ReconcileResult:
        """Create the index if needed and apply drift-free settings from ``self.schema``."""

    @abstractmethod
    def begin_rebuild(self, target: RebuildTarget) -> None:
        """
        Create and configure ``target`` from ``self.schema``.

        The library mints ``target`` and holds the rebuild lease before this
        call, so no other rebuild can create or clean a target for this use case
        at the same time. The adapter removes leftovers with the same index and
        version prefix, except ``target`` and the live index: a leftover would
        keep documents deleted after the crash if reused, and the live index
        must never be removed. On Typesense the live index is the collection
        the alias currently points at, and after a swap it carries the same
        prefix as the target.
        """

    @abstractmethod
    def commit_rebuild(self, target: RebuildTarget) -> None:
        """Wait for the target's writes, swap it in and delete the old target."""

    @abstractmethod
    def abort_rebuild(self, target: RebuildTarget) -> None:
        """Discard ``target`` after a failed rebuild."""

    @abstractmethod
    def upsert(self, documents: DocumentBatch, target: RebuildTarget | None = None) -> WriteReceipt:
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
    def mint_credential(self, scope: CredentialScope, expires_in: timedelta) -> SearchCredential:
        """Mint a scoped, expiring credential and the endpoint to use it against."""
