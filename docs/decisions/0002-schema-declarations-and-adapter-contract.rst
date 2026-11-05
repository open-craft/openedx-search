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
scope from ADR 0001. It does refine the registration shape from ADR 0001
Decision 1: index settings are derived from the declared schema, and query
shape belongs to the call rather than to the registration.

Terminology
***********

An **engine** is Meilisearch, Typesense or Algolia. An **adapter** is this
library's own implementation for one engine. The deployment's engine choice is
still called a backend in the configuration (``OPENEDX_SEARCH_BACKENDS``): a
backend is a named engine connection, not an adapter implementation. ADR 0001
uses "backend" for what this ADR calls an adapter.

Decision
********

1. One schema, many renderers
=============================

There is one authored declaration, ``IndexSchema``:

* identity: ``name`` and ``version``;
* ``fields``: a name, one of ``string``/``int32``/``int64``/``float``/``bool``/
  ``datetime``/``object``, ``array``,
  ``additional_properties`` and the capabilities;
* four capabilities: ``searchable`` (with ``order``), ``filterable``,
  ``facetable`` and ``sortable``.

The primary key is the required string field ``id``, which both Meilisearch and
Typesense use as the document identifier. A schema that omits ``id`` or declares
it as anything other than a required, non-array string is rejected. The field
cannot be ``searchable``, ``facetable`` or ``sortable``: if an ``id`` field is
sent, Typesense ignores it and only logs a warning, so those capabilities would
be dropped. The Typesense adapter omits ``id`` from the rendered ``fields``,
because Typesense reserves the name and always has an implicit primary key; the
neutral schema still declares it. Meilisearch uses the declared ``id`` as
``primaryKey``.

Sort precedence is fixed, not declared: an explicit sort always outranks keyword
relevance, and relevance applies only when the caller asks for it. Meilisearch
encodes it in the index's ``rankingRules`` with ``sort`` first, which
``reconcile`` applies. Typesense has no collection-level equivalent, so the
request builder chooses between ``_text_match:desc`` and a field sort, per ADR
0001's note that an explicit ``sort_by`` already outranks text matching. A
relevance query sends no sort parameter; a field sort sends the field and, for
Typesense, leaves ``_text_match:desc`` out.

``distinct`` is not modelled. The current consumer's ``distinctAttribute``
setting is being dropped, not carried over, so no use case needs it.

Only what a current consumer uses is modelled. Typo tolerance, tokenization,
stop words, synonyms, faceting and pagination limits, vectors, replicas and
custom ranking are absent until a consumer needs them. Course Discovery will
bring geo, custom ranking, unordered search and nested skills; the Typesense
adapter will bring nested fields and per-field flags.

``datetime`` is a logical type: values are timezone-aware ``datetime`` objects,
rendered as POSIX seconds (UTC) by the adapter, because neither engine has a
native date type.

Adapters render ``IndexSchema`` when they create or reconcile an index. There is
no second authored schema. A derived projection may exist internally, but it is
not committed as code or edited by hand: the authored ``IndexSchema`` is the
only declaration to maintain.

2. Registrations are engine-neutral
===================================

Each use case (e.g. course discovery, library content search, courseware search, ...) must be registered.
A "registration" declares one use case, and specifies the associated logical index and its schema:

::

    UseCaseRegistration
      use_case        str
      index           str            logical index name
      schema          IndexSchema

``index`` is a logical name. An adapter maps it to its own namespace: a
Meilisearch index UID, or a Typesense collection plus the alias used for
rebuilds.

One use case owns a logical index. Two use cases cannot share an index: a
rebuild of one builds a target from only its own documents and would drop the
other's on swap.

The engine is a deployment choice, configured once per deployment:

::

    OPENEDX_SEARCH_BACKENDS = {
        "default": {
            "engine": "meilisearch",
            "url": "...",
            "public_url": "...",
            "api_key": "...",
        },
        "typesense": {
            "engine": "typesense",
            "url": "...",
            "public_url": "...",
            "api_key": "...",
            "search_api_key": "...",
        },
    }
    OPENEDX_SEARCH_DEFAULT_BACKEND = "default"
    OPENEDX_SEARCH_USE_CASE_BACKENDS = {"forum": "typesense"}  # optional

Resolution is per use case: override, then deployment default, then an error.
The library ships no default engine.

``api_key`` is the key the adapter uses for writes. ``search_api_key`` is the
parent key for minting scoped search credentials, and Typesense requires it:
the parent of a scoped key must allow only ``documents:search``, so the write key
cannot mint one. Meilisearch mints tenant tokens from ``api_key`` and can omit
it. ``url`` is the connection URL the backend uses, while ``public_url`` is the
one the browser uses and defaults to ``url``. They differ when the engine is
reachable internally but exposed through a proxy or a TLS terminator.
``engine``, ``url`` and ``api_key`` are required, ``search_api_key`` and
``public_url`` are optional, and any other key is rejected. The entry is typed
rather than a free-form mapping, so a typo fails when the backend is resolved
instead of being ignored until the first write.

Registrations are loaded by the consumer from its own ``AppConfig.ready()``.
The library does not scan packages or import registration modules.

3. Adapters declare capabilities and share validation
=====================================================

Each adapter implements ``openedx_search.adapters.Adapter``:

::

    validate_schema()                                      -> None or raises UnsupportedSchema
    reconcile()                                            -> ReconcileResult
    begin_rebuild(target)                                  -> None
    commit_rebuild(target)                                 -> None
    abort_rebuild(target)                                  -> None
    upsert(documents, target=None)                         -> WriteReceipt
    delete(primary_keys=None, *, filter=None, target=None) -> WriteReceipt
    check_write(receipt)                                   -> WriteReceipt
    search(query)                                          -> SearchResult
    mint_credential(scope, expires_in)                     -> SearchCredential

A schema passes two checks, at different times. The first needs no engine and no
settings: the neutral model rejects declarations that no supported engine can
represent, and it does so when ``IndexSchema`` is constructed, before any
registration exists. A ``searchable`` field must be a string or an object whose
leaves are strings, and an array field cannot be ``sortable``. A field inside an
object array is itself an array, because Typesense requires array types there: a
declaration whose ancestor chain includes an ``array`` object must set
``array``, and since arrays cannot be ``sortable``, a sortable leaf under an
object array is rejected. A required field must have a required parent chain: a
required ``a.b`` under an optional ``a`` is rejected, because Typesense rejects
a document that omits a non-optional nested field even when the parent is
absent.

The second is ``Adapter.validate_schema()``, which covers what the first cannot:
capabilities the neutral model allows but this particular adapter cannot
represent. Every adapter implements it. It is a no-op for Meilisearch and
Typesense, and the hook for an adapter with a narrower feature set, which raises
``UnsupportedSchema`` naming the parts it cannot represent.

``register_use_case()`` does not call ``validate_schema()``; it validates the
registration object and its index ownership only, so the registry never reads
settings or imports an adapter. A Django system check resolves each
registration's adapter and calls ``validate_schema()``, so an unsupported
schema fails ``manage.py check`` and the deploy-time commands that run checks
(``migrate`` among them), not silently at query time and not at registration.

For Meilisearch and Typesense:

.. code-block:: python

    def validate_schema(self):
        pass

An adapter with a narrower feature set makes the check explicit. For example, an
adapter that cannot represent nested objects:

.. code-block:: python

    def validate_schema(self):
        missing = {
            field.name
            for field in self.schema.fields
            if field.type is FieldType.OBJECT or "." in field.name
        }
        if missing:
            raise UnsupportedSchema(
                f"{self.backend.engine} cannot represent: {', '.join(sorted(missing))}"
            )

Document validation is a separate, library-level step. Before a document is
handed to an adapter, the library validates it against the schema, so every
engine accepts and rejects the same documents. The engines disagree on their
own: Meilisearch ignores fields it does not know and accepts a document that
omits a field, while Typesense drops undeclared fields and rejects a document
that omits a field it rendered as ``optional: false``. The library relies on
neither behavior.

A document is valid when:

* every ``required`` field is present, including the ``id`` the schema requires;
* the ``id`` value matches ``[A-Za-z0-9_-]+`` and is at most 468 bytes, the
  intersection of the two engines and the filterable limit, because ``id`` may
  be filterable. Meilisearch rejects a colon and fails the whole batch, so an
  opaque key is derived with ``document_id_from_key`` before it is indexed; the
  library validates the id and does not rewrite the document;
* every declared field that is present has the declared type and shape, and an
  array field is an array of that type;
* a ``datetime`` value is accepted for a ``DATETIME`` field and rendered as
  POSIX seconds, which is how the engines store it;
* a nested object follows the same rules for its declared leaves. Under
  ``additional_properties`` any key is accepted, and its value must be a string;
* every field in the document is declared in the schema.

``null`` is not a value. A consumer that has nothing to store omits the field
instead of sending ``null``, so a null is an error for required and optional
fields alike.

Free-form values are string-only because the engines diverge otherwise.
Meilisearch indexes a number or a boolean and returns it from a text search,
while Typesense searches only strings. Typesense also pins the type of the first
value it sees for a free-form key and rejects a later document that uses a
different one, which Meilisearch accepts. Strings keep the accepted documents
and the search results the same on both engines.

A violation raises ``DocumentError`` naming the document's ``id``, the field
and the reason. Validation runs before the batch reaches the adapter, so one
invalid document fails the whole ``upsert`` call before anything is written,
and adapters never see an invalid document.

Fields are optional by default. ``required`` is what the schema declares and
what Typesense renders as ``optional: false``; the validator enforces it, not
the engine.

``reconcile`` creates the index if it is missing, compares the declared settings
with the live index on every deploy, and applies engine-compatible changes
without reindexing documents. On Meilisearch that includes the ``rankingRules``
with ``sort`` first. ``begin_rebuild`` creates and configures its
target using the same renderer. A version bump or an incompatible settings
change requires the explicit rebuild in section 4. The adapter owns the
mechanics (Meilisearch PATCH and reindex, Typesense new collection plus alias).
Lossy edges are reported by ``validate_schema`` or ``reconcile``, never dropped.

``upsert`` and ``delete`` submit one bounded batch and return a
``WriteReceipt``. ``upsert`` takes one bounded batch, a sequence of documents.
``delete`` takes ``primary_keys`` values of the required string ``id`` field
(one value or a sequence) or the same abstract filter expression ``search``
accepts. Exactly one of ``primary_keys`` and ``filter`` is required, and
neither may be empty, so a bare ``delete()`` cannot empty an index. Primary keys
are checked against the same id rule before they reach the adapter. Both methods
accept an optional ``target`` so the library can submit to a rebuild target.

``check_write(receipt)`` performs one status check and returns an updated
receipt, raising ``WriteFailed`` when the engine reports a failure. It never
sleeps or retries; scheduling and backoff belong to the indexing worker, per
ADR 0001 Decision 9. A synchronous engine returns an already-complete receipt
without a request. Typesense's import endpoint is synchronous, but it returns
HTTP 200 even when a document is rejected. The adapter reads every result line
and raises ``WriteFailed`` when any line reports ``"success": false``, so a 200
alone never marks a batch complete.

``begin_rebuild(target)`` creates and configures the target from
``self.schema``; the library mints the ``RebuildTarget(index, version,
generation)`` and holds the lease before the call. The generation is unique to
the rebuild and is not the schema version, so a second rebuild at the same
version derives a different target name. ``begin_rebuild`` removes leftovers
with the same index and version prefix, except ``target`` and the live index. On
Typesense the live index is the collection the alias points at, and after a swap
it carries the same prefix, so a plain prefix delete would remove it.
``commit_rebuild(target)`` waits for the target's writes, swaps it into place
and deletes the old target. ``abort_rebuild(target)`` discards it and never
touches the lease. The library orchestrates these primitives in section 4.

``search`` and ``mint_credential`` are the read-side adapter methods; section 4
describes their consumer contract.

4. Consumers use a small API
============================

Consumers do not talk to adapters directly. ``openedx_search.api`` resolves the
registration and backend and exposes these functions:

::

    search(use_case, query)                             -> SearchResult
    mint_credential(use_case, scope, expires_in)        -> SearchCredential
    upsert(use_case, documents)                         -> WriteReceipt
    delete(use_case, primary_keys=None, *, filter=None) -> WriteReceipt
    check_write(use_case, receipt)                      -> WriteReceipt
    rebuild(use_case, document_source)                  -> RebuildResult

The consumer functions delegate to the adapter contract in section 3. The
library adds only the behaviour below.

.. list-table::
   :header-rows: 1

   * - Consumer API
     - Adapter methods
     - Library-owned behaviour
   * - ``search``
     - ``search``
     - none
   * - ``mint_credential``
     - ``mint_credential``
     - none
   * - ``upsert``
     - ``upsert``
     - one bounded batch (a sequence); validate every document; dual-write
       during rebuild
   * - ``delete``
     - ``delete``
     - normalize ``primary_keys``; exactly one form; dual-write
   * - ``check_write``
     - ``check_write``
     - none; the worker owns backoff
   * - ``rebuild``
     - ``begin_rebuild``, ``commit_rebuild`` / ``abort_rebuild``
     - stream source; dual-write; accepted race

``search`` is for maintenance and conformance, not the frontend search path:
the browser queries the engine directly with a scoped credential, per ADR 0001
Decision 7. Query terms are per request, not part of the schema.

Filters are exact matches. ``FilterExpression`` is a sequence of
``FilterGroup`` values combined with AND, and each group holds ``FilterTerm``
values combined with OR. Studio's tenant filter is one group with ``org IN
[...]`` and ``access_id IN [...]``: the fields are alternatives, not
requirements. Adapters render the expression in their own syntax, and raw engine
filters never cross the API. Values are strings, integers, floats, booleans or a
timezone-aware ``datetime``. A datetime is rendered as POSIX seconds, the same
type documents accept. ``None`` is not a value: documents carry no nulls, and
"field is absent"
is a separate operator with its own engine syntax. The model covers exact
matches combined with AND and
OR. Negation, ranges and field-to-field comparisons are out of scope until a
consumer needs one; adding it extends ``FilterTerm`` rather than the expression
shape.

``mint_credential`` takes a ``CredentialScope`` of tenant filters and a
positive ``expires_in`` lifetime as a ``timedelta``, and returns a
``SearchCredential``. It is derived locally (Meilisearch tenant tokens,
Typesense scoped search keys), so Django is not in the search path. The adapter
converts ``expires_in`` to the engine's absolute expiry (Typesense's Unix
timestamp, Meilisearch's expiry date). For Typesense the key embeds ``query_by``
and ``query_by_weights`` from the schema, and deliberately not ``sort_by``:
embedded parameters cannot be overridden, and the sort is a per-query choice.

``SearchCredential`` carries ``url``, ``index_name`` and ``api_key``. A bare key
is not enough for the browser: ``url`` is the backend's ``public_url``, a
separate setting from the connection ``url``, and the physical index name
belongs to the adapter, so a consumer cannot derive either.

``upsert`` and ``delete`` take one bounded batch. ``documents`` is a sequence
because a reindex in progress submits the same batch twice. The API normalizes
a single primary key to a tuple and otherwise uses the delete forms from
section 3. During a reindex, both functions submit to the live index and the
target when the target carries the same schema version.

``check_write`` delegates one poll to the adapter; the worker owns retry and
backoff (section 3).

``rebuild`` resolves the registration, mints a ``RebuildTarget`` from the
logical index, the schema version and a unique generation, and acquires the
lease with one atomic ``cache.add`` before creating anything. A second rebuild
fails with ``RebuildInProgress`` and touches no index. Only after the lease is
held does the library call ``begin_rebuild(target)``, so the cleanup it performs
cannot race another rebuild. The cache key is per use case and holds the
``RebuildTarget``, whose generation is the lease owner. The lease TTL is five
minutes and is refreshed with ``cache.touch`` between batches; when the key is
gone the worker stops writing to the target, because a newer rebuild may have
cleaned it up. Before ``commit_rebuild`` it reads the key and compares the stored
generation with its own target; a missing or different generation means another
rebuild took over, so it aborts instead of swapping a target that may have missed
concurrent writes. The library releases the lease after ``commit_rebuild`` or
``abort_rebuild``, and only when the stored value is still its own target;
``abort_rebuild`` discards the target and never touches the cache. A writer
reads the key on every write; no key means no active rebuild. It dual-writes
only when the target's version equals its registration's version; a mismatch
raises ``SchemaVersionMismatch`` (see below). The cache is selected by
``OPENEDX_SEARCH_CACHE_ALIAS``, which defaults to ``default``, and must be shared
across writer processes, so the process-local cache is not sufficient. Use a
dedicated Redis cache for this alias: the platform default is memcached, and an
eviction there aborts the rebuild instead of corrupting it. Redis persistence
keeps the lease across a cache restart, but it is not a checkpoint: a crashed
rebuild is discarded and restarts from the beginning, and the lease TTL bounds
how long it blocks the next rebuild.

The source is a callable returning an iterator of batches, invoked in the rebuild
worker, so a consumer can page through its database without materializing the
corpus. Each batch goes to the target and is confirmed with ``check_write``
before the next is requested. While a same-version target is recorded, ``upsert``
and ``delete`` submit to both the live index and the target.
``commit_rebuild`` swaps the target in and deletes the old target; any failure
aborts it.

A reindex is not a consistent snapshot. The document source reads from the
consumer's database while writes continue. If a write reaches the target before
the source writes the same document from an older read, the source's version
wins in the target. A delete followed by the source yielding the deleted
document has the same effect. The window is bounded by the time between the
source's read and its write to the target. This matches the current
``content/search`` rebuild and the dual-write recipe Typesense documents for
zero-downtime reindexing. A migration has no such window because its writers are
stopped.

Rebuilds and schema versions
----------------------------

There are two rebuilds, and the registration version decides which one is safe:

* **Reindex, same version.** The live index and the target accept the same
  documents, so ``upsert`` and ``delete`` dual-write while the rebuild runs. No
  writer stops, and the swap is invisible to readers. This applies to data
  repairs, a settings change that needs a full reindex, and source changes.
* **Migration, new version.** The live index and the target do not accept the
  same documents, so no process may write during the rebuild. Stop every writer,
  deploy the new code, run the rebuild to completion, commit the swap, then
  resume writes.

Reads keep serving the old index during a migration; a consumer whose read path
depends on the new shape extends the window to reads as well. Stop writers
before deploying, because a new writer matches the target version and would
dual-write against the old live index.

The version guard separates the two: ``upsert`` and ``delete`` dual-write only
when the recorded target's version equals their registration's version. A
mismatch raises ``SchemaVersionMismatch`` in the writer. It does not write a
stale shape, does not drop the write, and does not abort the rebuild. An old
writer that survives a migration whose pause was incomplete fails loudly instead
of corrupting the target.

Consequences
************

1. One schema is authored, versioned, validated and tested; a new use case is a
   declaration, not another engine integration.
2. Adapters stay small: capabilities, a renderer, document mapping and
   transport.
3. Unsupported capabilities fail during deployment, before data is indexed.
4. Adding Algolia remains additive: capability flags plus a renderer.
5. A reindex is not point-in-time consistent: a document changed between the
   source's read and its write can be indexed in its older form in the target.
   The next write to that document, or a follow-up rebuild, corrects it. A
   migration has no such window because its writers are stopped.

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

**Permissive searchable objects with adapter expansion.** A searchable object
could allow non-string leaves and have the adapter expand it into its string
leaves:

.. code-block:: python

    Field("tags", FieldType.OBJECT, capabilities=Capabilities(searchable=True))
    Field("tags.count", FieldType.INT64, capabilities=Capabilities(filterable=True))
    Field("tags.taxonomy", FieldType.STRING)

Meilisearch would render ``searchableAttributes: ["tags.taxonomy"]`` and
Typesense ``query_by=tags.taxonomy``. Without that expansion, Meilisearch
includes ``tags.count`` when ``tags`` is searchable, while Typesense ignores it.
Rejected because parity would depend on every adapter applying the expansion,
and free-form ``additional_properties`` objects cannot be expanded. Free-form
values are string-only for the same reason: a number is searchable on
Meilisearch and not on Typesense, and Typesense pins the type of the first value
it sees for a free-form key.

The strict rule instead requires a searchable object to contain only string
leaves. A mixed object marks its string children searchable:

.. code-block:: python

    Field("tags", FieldType.OBJECT)
    Field("tags.count", FieldType.INT64, capabilities=Capabilities(filterable=True))
    Field("tags.taxonomy", FieldType.STRING, capabilities=Capabilities(searchable=True))

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
