"""Shared test doubles for the API and adapter tests."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from openedx_search.adapters import (
    Adapter,
    CredentialScope,
    DocumentBatch,
    RebuildTarget,
    ReconcileResult,
    SearchCredential,
    SearchResult,
    WriteReceipt,
)
from openedx_search.registry import UseCaseRegistration
from openedx_search.schema import Field, FieldType, IndexSchema


class FakeAdapter(Adapter):
    """An adapter that records calls instead of talking to an engine."""

    def __init__(self, registration, backend):
        """Store the registration, backend and an empty call log."""
        super().__init__(registration, backend)
        self.calls: list[tuple[Any, ...]] = []

    def validate_schema(self) -> None:
        """Accept every schema."""

    def reconcile(self) -> ReconcileResult:  # pragma: no cover - not called by the API tests
        """Report no change."""
        return ReconcileResult(index=self.index, version=self.schema.version)

    def begin_rebuild(self, target: RebuildTarget) -> None:  # pragma: no cover - not called by the API tests
        """Record the target creation."""
        self.calls.append(("begin_rebuild", target))

    def commit_rebuild(self, target: RebuildTarget) -> None:  # pragma: no cover - not called by the API tests
        """Record the swap."""
        self.calls.append(("commit_rebuild", target))

    def abort_rebuild(self, target: RebuildTarget) -> None:  # pragma: no cover - not called by the API tests
        """Record the abort."""
        self.calls.append(("abort_rebuild", target))

    def upsert(self, documents: DocumentBatch, target: RebuildTarget | None = None) -> WriteReceipt:
        """Record the batch."""
        self.calls.append(("upsert", tuple(documents), target))
        return WriteReceipt(complete=True)

    def delete(
        self,
        primary_keys=None,
        *,
        filter=None,  # pylint: disable=redefined-builtin
        target: RebuildTarget | None = None,
    ) -> WriteReceipt:
        """Record the delete."""
        self.calls.append(("delete", primary_keys, filter, target))
        return WriteReceipt(complete=True)

    def check_write(self, receipt: WriteReceipt) -> WriteReceipt:
        """Record the poll and report completion."""
        self.calls.append(("check_write", receipt))
        return WriteReceipt(complete=True)

    def search(self, query: Any) -> SearchResult:  # pragma: no cover - not called by the API tests
        """Return no hits."""
        return SearchResult(documents=(), total=0)

    def mint_credential(  # pragma: no cover - not called by the API tests
        self,
        scope: CredentialScope,
        expires_in: timedelta,
    ) -> SearchCredential:
        """Return a fixed credential."""
        return SearchCredential(url="http://fake", index_name=self.index, api_key="key")


SIMPLE_SCHEMA = IndexSchema(
    name="simple",
    fields=(
        Field("id", FieldType.STRING, required=True),
        Field("title", FieldType.STRING, required=True),
    ),
)

SIMPLE_REGISTRATION = UseCaseRegistration(
    use_case="simple",
    index="simple_index",
    schema=SIMPLE_SCHEMA,
)
