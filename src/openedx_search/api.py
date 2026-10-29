"""
Consumer-facing API for openedx-search.

ADR 0002, Decision 4: consumers use this module rather than talking to an
adapter. The functions resolve the registration and backend internally. The
registry helpers in ``openedx_search.registry`` are not part of this surface.

These functions are currently signatures and contracts only; the
implementations land with the engine adapters.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, TypeAlias

from .adapters import (
    CredentialScope,
    DocumentBatch,
    FilterExpression,
    PrimaryKey,
    RebuildTarget,
    SearchResult,
    WriteReceipt,
)

__all__ = [
    "CredentialScope",
    "DocumentSource",
    "RebuildInProgress",
    "RebuildResult",
    "check_write",
    "delete",
    "mint_credential",
    "rebuild",
    "search",
    "upsert",
]

DocumentSource: TypeAlias = Callable[[], Iterable[DocumentBatch]]


class RebuildInProgress(RuntimeError):
    """A rebuild is already running for the use case."""


@dataclass(frozen=True)
class RebuildResult:
    """Summary of a completed rebuild."""

    target: RebuildTarget
    documents: int


def upsert(use_case: str, documents: DocumentBatch) -> WriteReceipt:
    """
    Submit one bounded batch of documents for upsert.

    ``documents`` is a sequence, not a one-shot iterator, because a rebuild in
    progress submits the same batch to both the live index and the target. The
    returned receipt covers the live index; the rebuild confirms the target's
    copy before it swaps.

    During a rebuild the batch is also submitted to the rebuild target. A
    failure in either submission is raised to the caller. The caller may retry
    with the same batch, since the operation is an upsert.
    """
    raise NotImplementedError("upsert is implemented with the engine adapters")


def _normalize_primary_keys(
    primary_keys: PrimaryKey | Sequence[PrimaryKey] | None,
) -> tuple[PrimaryKey, ...] | None:
    """Normalize one primary key or a sequence into a non-empty tuple."""
    if primary_keys is None:
        return None
    if isinstance(primary_keys, str):
        return (primary_keys,)
    keys = tuple(primary_keys)
    if not keys:
        raise ValueError("primary_keys must not be empty")
    return keys


def delete(
    use_case: str,
    primary_keys: PrimaryKey | Sequence[PrimaryKey] | None = None,
    *,
    filter: FilterExpression | None = None,  # pylint: disable=redefined-builtin
) -> WriteReceipt:
    """
    Submit a delete by primary key or by an abstract filter expression.

    ``primary_keys`` are values of the required string ``id`` field: one value
    for a single document, a sequence for a batch. A string is treated as one
    key, not as a sequence of characters. ``filter`` is the same abstract
    filter expression used by search, never a raw engine string.

    Exactly one of ``primary_keys`` and ``filter`` is required, and neither may
    be empty, so a bare call cannot empty an index. During a rebuild the delete
    is also submitted to the rebuild target.
    """
    raise NotImplementedError("delete is implemented with the engine adapters")


def check_write(use_case: str, receipt: WriteReceipt) -> WriteReceipt:
    """
    Perform one status check for a submitted write.

    This never sleeps or retries. Scheduling and backoff belong to the indexing
    worker, per ADR 0001 Decision 9. A synchronous engine returns an
    already-complete receipt without a request. The worker calls this until the
    receipt is complete and raises when the engine reports a failed write.
    """
    raise NotImplementedError("check_write is implemented with the engine adapters")


def rebuild(use_case: str, document_source: DocumentSource) -> RebuildResult:
    """
    Rebuild a use case's index from a streamed document source.

    ``document_source`` is a zero-argument callable returning an iterator of
    document batches. It is invoked in the rebuild worker and consumed one
    batch at a time, so the consumer can page through its database without
    materializing the corpus. Paging, resumability and progress reporting
    belong to the source.

    The library creates the target, records it so concurrent writes reach both
    the live index and the target, confirms each source batch, and swaps the
    target in only after its writes are complete. A failure aborts the target.

    A rebuild is not a consistent snapshot. If a write reaches the target
    before the source writes the same document from an older read, the
    source's older version wins in the target. A delete can be undone the same
    way if the source yields the deleted document afterwards. This is the same
    window the current ``content/search``
    rebuild has; callers that need a strict snapshot must stop writes for the
    duration of the rebuild.
    """
    raise NotImplementedError("rebuild is implemented with the engine adapters")


def search(use_case: str, query: Any) -> SearchResult:
    """
    Run one query through the adapter.

    This is for maintenance and conformance, not the frontend search path. The
    browser queries the engine directly with a scoped credential, so this does
    not proxy the frontend's searches. The query model lands with the query API.
    """
    raise NotImplementedError("search is implemented with the engine adapters")


def mint_credential(  # pylint: disable=unused-argument
    use_case: str, scope: CredentialScope, expires_in: timedelta
) -> str:
    """
    Mint a scoped, expiring credential for direct frontend queries.

    ``scope`` is a :class:`CredentialScope` carrying the tenant filters.
    ``expires_in`` is the positive lifetime of the credential. The adapter
    converts it to the engine's absolute expiry (Typesense's Unix timestamp,
    Meilisearch's expiry date). The credential is derived locally; no request is
    proxied through Django.
    """
    if expires_in <= timedelta(0):
        raise ValueError("expires_in must be positive")
    raise NotImplementedError("mint_credential is implemented with the engine adapters")
