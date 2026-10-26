0002 Use-Case Schema Declarations and Adapter Contract
#######################################################

Status
******

**Draft** (for upstream discussion)

Context
*******

`0001-purpose-of-this-repo.rst`_ establishes ``openedx-search`` as the single
search library for the platform, with use cases declaring a document schema,
index settings and a query shape, and Typesense and Meilisearch implemented.
This ADR settles what that leaves open: the concrete declaration, how adapters
render it, and how a use case selects an engine. It does not revisit the engine
scope or the registration model from ADR 0001.

Terminology
***********

An **engine** is Meilisearch, Typesense or Algolia. An **adapter** is this
library's own implementation for one engine. The deployment's engine choice is
still called a backend in the configuration (``OPENEDX_SEARCH_BACKENDS``), and
ADR 0001 uses "backend" for what this ADR calls an adapter.

Decision
********

1. One schema, many renderers
=============================

There is one authored declaration, ``IndexSchema``:

* identity: ``name``, ``primary_key`` and ``version``;
* ``fields``: a name, one of ``string``/``int32``/``int64``/``float``/``bool``/
  ``datetime``/``geopoint``/``object``/``unknown``, ``array``,
  ``additional_properties`` and the capabilities;
* four capabilities: ``searchable`` (with ``order``), ``filterable``,
  ``facetable`` and ``sortable``;
* ``ranking`` rules and an optional ``distinct`` attribute.

Only what a current consumer uses is modelled. Typo tolerance, tokenization,
stop words, synonyms, faceting and pagination limits, vectors, replicas and
custom ranking are absent until a consumer needs them. Course Discovery will
bring geo, custom ranking, unordered search and nested skills; the Typesense
adapter will bring nested fields and per-field flags.

``datetime`` is a logical type: values are POSIX timestamps in seconds (UTC),
stored as numbers, because neither engine has a native date type.

Adapters render ``IndexSchema`` in ``configure(schema)``. There is no second
authored schema. A derived projection may exist internally, but must not become
another type to maintain.

2. Registrations are engine-neutral
===================================

A registration names the use case, the logical index and the schema:

::

    UseCaseRegistration
      use_case        str
      index           str            logical index name
      schema          IndexSchema

``index`` is a logical name. An adapter maps it to its own namespace: a
Meilisearch index UID, or a Typesense collection plus the alias used for
rebuilds.

The engine is a deployment choice, configured once per deployment:

::

    OPENEDX_SEARCH_BACKENDS = {
        "default": {"engine": "meilisearch", "url": "...", "api_key": "..."},
    }
    OPENEDX_SEARCH_DEFAULT_BACKEND = "default"
    OPENEDX_SEARCH_USE_CASE_BACKENDS = {"forum": "typesense"}  # optional

Resolution is per use case: override, then deployment default, then an error.
The library ships no default engine.

Registrations are loaded by the consumer from its own ``AppConfig.ready()``.
The library does not scan packages or import registration modules.

Query contracts are separate from the schema. Callers build filter terms and
adapters render them; raw filter strings do not cross the API. ``search`` is for
maintenance and conformance, not the Studio browser path, which queries the
engine directly with a scoped credential.

3. Adapters declare capabilities and share validation
=====================================================

Each adapter implements ``openedx_search.adapters.Adapter``:

::

    validate_schema(schema)                                -> None or raises UnsupportedSchema
    configure(schema)                                      -> renders and applies settings
    reconcile(schema)                                      -> ReconcileResult
    begin_rebuild(schema)                                  -> RebuildTarget
    commit_rebuild(target)                                 -> None
    abort_rebuild(target)                                  -> None
    upsert(documents, target=None)                         -> WriteReceipt
    delete(primary_keys=None, *, filter=None, target=None) -> WriteReceipt
    check_write(receipt)                                   -> WriteReceipt
    search(query)                                          -> SearchResult
    mint_credential(scope, expires)                        -> str

Each adapter implements ``validate_schema(schema)``. The shared model already
rejects declarations that either implemented engine cannot represent: a
``distinct`` attribute must be ``facetable``, a ``searchable`` field must be a
string or an object whose leaves are strings, and an array field cannot be
``sortable``. Typesense's ``group_by`` requires a facet field; see
`the Typesense search API
<https://typesense.org/docs/30.2/api/search.html#group_by>`_.
``validate_schema`` is therefore a no-op for the Meilisearch and Typesense
adapters once they are implemented. It is the hook for an adapter with a
narrower feature set, which raises ``UnsupportedSchema`` naming the parts it
cannot represent. It runs at registration and before settings are applied; a use
case that needs an unsupported capability fails there, not silently.

For the two adapters that will be implemented the method will be a no-op:

.. code-block:: python

    def validate_schema(self, schema):
        pass

An adapter with a narrower feature set makes the check explicit. For example, an
adapter that cannot represent ``geopoint`` fields:

.. code-block:: python

    def validate_schema(self, schema):
        missing = {
            field.name
            for field in schema.fields
            if field.type is FieldType.GEOPOINT
        }
        if missing:
            raise UnsupportedSchema(
                f"{self.name} cannot represent: {', '.join(sorted(missing))}"
            )

Document validation is a separate, library-level step. Before a document is
handed to any adapter, the library checks it against the schema: field types,
arrays, required fields and free-form objects. Adapters do not reimplement this,
so every engine rejects the same bad document for the same reason.

Fields are optional by default. A ``required`` field must be present and
non-null; Typesense renders it as ``optional: false``, and the shared validator
rejects documents that omit it.

``upsert`` and ``delete`` submit one bounded batch and return a
``WriteReceipt``. ``upsert`` takes an iterable of documents. ``delete`` takes
``primary_keys`` values of the declared primary key field (one value or a
sequence) or the same abstract filter expression ``search`` accepts. Exactly one
of ``primary_keys`` and ``filter`` is required, and neither may be empty, so a
bare ``delete()`` cannot empty an index. Both methods accept an optional
``target`` so the library can submit to a rebuild target.

``check_write(receipt)`` performs one status check and returns an updated
receipt, raising ``WriteFailed`` when the engine reports a failure. It never
sleeps or retries; scheduling and backoff belong to the indexing worker, per
ADR 0001 Decision 9. A synchronous engine returns an already-complete receipt
without a request.

A rebuild is a library operation, not a single adapter call. ``begin_rebuild``
creates the target and returns a ``RebuildTarget``; ``commit_rebuild`` waits for
the target's writes, swaps it into place and deletes the old target; and
``abort_rebuild`` discards it. The library owns the orchestration and the
target's shared state, because writes submitted while a rebuild runs must reach
both the live index and the target. A registration does not own a document
source, so the caller supplies one. Rebuilds are worker-only and versioned by
``schema.version``.

A rebuild is not a consistent snapshot. The document source reads from the
consumer's database while writes continue. If a write reaches the target before
the source writes the same document from an older read, the source's version
wins in the target. A delete followed by the source yielding the deleted
document has the same effect. The window is bounded by the time between the
source's read and its write to the target. This matches the current
``content/search`` rebuild and the dual-write recipe Typesense documents for
zero-downtime reindexing. This ADR accepts the window rather than adding a
delta log; callers that need a strict snapshot must stop writes for the
duration of the rebuild.

``reconcile`` compares the declared settings with the live index on every
deploy and applies engine-compatible changes without reindexing documents. A
version bump or an incompatible settings change requires the explicit rebuild
above. The adapter owns the mechanics (Meilisearch PATCH and reindex, Typesense
new collection plus alias). Lossy edges are reported by ``validate_schema`` or
``configure``, never dropped.

4. Consumers use a small write-first API
========================================

Consumers do not talk to adapters directly. ``openedx_search.api`` resolves the
registration and backend and exposes four functions:

::

    upsert(use_case, documents)                         -> WriteReceipt
    delete(use_case, primary_keys=None, *, filter=None) -> WriteReceipt
    check_write(use_case, receipt)                      -> WriteReceipt
    rebuild(use_case, document_source)                  -> RebuildResult

``upsert`` and ``delete`` submit one bounded batch. ``documents`` is a sequence
because a rebuild in progress submits the same batch twice. ``primary_keys``
accepts one value or a sequence; the API normalizes it. ``filter`` is the same
abstract expression as ``search``. Exactly one of ``primary_keys`` and
``filter`` is required, and neither may be empty.

``check_write`` performs one poll of the receipt's engine task. Retry and
backoff belong to the worker. ``rebuild`` is worker-only.

``rebuild`` resolves the registration, calls ``begin_rebuild``, records the
target in shared state, and iterates ``document_source``. The source is a
callable returning an iterator of batches, invoked in the rebuild worker, so a
consumer can page through its database without materializing the corpus. Each
batch goes to the target and is confirmed with ``check_write`` before the next
is requested. While the target is recorded, ``upsert`` and ``delete`` submit to
both the live index and the target. ``commit_rebuild`` swaps the target in and
deletes the old target; any failure aborts it.

Consequences
************

1. One schema is authored, versioned, validated and tested; a new use case is a
   declaration, not another engine integration.
2. Adapters stay small: capabilities, a renderer, document mapping and
   transport.
3. Unsupported capabilities fail at registration or startup, before data is
   indexed.
4. Adding Algolia remains additive: capability flags plus a renderer.
5. A rebuild is not point-in-time consistent: a document changed between the
   source's read and its write can be indexed in its older form in the target.
   The next write to that document, or a follow-up rebuild, corrects it.

Alternatives Considered
***********************

**Narrow adapter contract first.** The initial contribution (``#3``/``#4``)
uses a flat, string-only ``IndexDefinition`` with exact filters and bounded
pagination. It ships a small, real-engine-verified slice early, but cannot
express Studio's typed and nested documents; it would either grow into
``IndexSchema`` or cap use cases.

**Separate document schema and settings models.** Splitting ``IndexSchema``
mirrors ADR 0001's wording, but field capabilities drive settings in every
engine (``facetable`` becomes ``facet: true``, ``sortable`` becomes a per-field
sort), so the two must stay in sync.

References
**********

.. _0001-purpose-of-this-repo.rst: ./0001-purpose-of-this-repo.rst

* ADR 0001, "Purpose of This Repo" - ``docs/decisions/0001-purpose-of-this-repo.rst``
* ``content/search`` ADR 0001, "Studio Content Search Powered by Meilisearch" -
  https://github.com/openedx/openedx-platform/blob/master/openedx/core/djangoapps/content/search/docs/decisions/0001-meilisearch.rst
* ``content/search`` ADR 0002, "Separate Meilisearch Indexes for Course and
  Library Content" -
  https://github.com/openedx/openedx-platform/blob/master/openedx/core/djangoapps/content/search/docs/decisions/0002-separate-course-and-library-indexes.rst
* ``openedx/openedx-platform#38993``, indexing performance and asynchronous
  updates - https://github.com/openedx/openedx-platform/issues/38993
* ``openedx/modular-learning#245``, "Prototype Typesense search support" -
  https://github.com/openedx/modular-learning/issues/245
* ``openedx/openedx-search#3``, Meilisearch and Typesense document adapters -
  https://github.com/openedx/openedx-search/pull/3
* ``openedx/openedx-search#4``, library-search correctness benchmark -
  https://github.com/openedx/openedx-search/pull/4
