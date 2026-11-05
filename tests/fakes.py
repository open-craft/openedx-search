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
        super().__init__(registration, backend)
        self.calls: list[tuple[Any, ...]] = []

    def validate_schema(self) -> None:
        """Accept every schema."""

    def reconcile(self) -> ReconcileResult:
        """Report no change."""
        return ReconcileResult(index=self.index, version=self.schema.version)

    def begin_rebuild(self) -> RebuildTarget:
        """Return a fixed target."""
        return RebuildTarget(index=self.index, version=self.schema.version, generation="test")

    def commit_rebuild(self, target: RebuildTarget) -> None:
        """Record the swap."""
        self.calls.append(("commit_rebuild", target))

    def abort_rebuild(self, target: RebuildTarget) -> None:
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
        """Return the receipt unchanged."""
        return receipt

    def search(self, query: Any) -> SearchResult:
        """Return no hits."""
        return SearchResult(documents=(), total=0)

    def mint_credential(self, scope: CredentialScope, expires_in: timedelta) -> SearchCredential:
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
