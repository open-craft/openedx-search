0002 Use-Case Schema Declarations and Backend Contract
#######################################################

Status
******

**Draft** (for upstream discussion)

Context
*******

`0001-purpose-of-this-repo.rst`_ establishes ``openedx-search`` as the single
search library for the platform, with use cases declaring a document schema,
index settings and a query shape, and Typesense and Meilisearch implemented.
This ADR settles what that leaves open: the concrete declaration, how backends
render it, and how a use case selects a backend. It does not revisit the engine
scope or the registration model from ADR 0001.

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
backend will bring nested fields and per-field flags.

Backends render ``IndexSchema`` in ``configure(schema)``. There is no second
authored schema. A derived projection may exist internally, but must not become
another type to maintain.

2. Registrations are engine-neutral
===================================

A registration names the use case, the logical index, the schema and its query
shape:

::

    UseCaseRegistration
      use_case        str
      index           str            logical index name
      schema          IndexSchema
      query_shape     QueryShape     filters, facets, sort, defaults

``index`` is a logical name. A backend maps it to its own namespace: a
Meilisearch index UID, or a Typesense collection plus the alias used for
rebuilds.

The backend is a deployment choice, configured once per deployment:

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
backends render them; raw filter strings do not cross the API. ``search`` is for
maintenance and conformance, not the Studio browser path, which queries the
engine directly with a scoped credential.

3. Backends declare capabilities and share validation
=====================================================

Each backend implements:

::

    capabilities()                    -> feature flags
    validate(schema)                  -> raises UnsupportedSchema
    configure(schema)                 -> renders and applies settings
    upsert(documents)                 -> WriteReceipt
    search(query)                     -> SearchResult
    mint_credential(scope, expires)   -> str
    reconcile(schema)                 -> ReconcileResult

``validate`` runs at registration and before settings are applied; a use case
that needs an unsupported capability fails there, not silently. The library
validates documents against ``IndexSchema`` before handing them to a backend, so
every engine rejects the same bad document for the same reason.

Reconciliation is versioned and runs on every deploy; the backend owns its
mechanics (Meilisearch PATCH and reindex, Typesense new collection plus alias).
Lossy edges are reported by ``validate`` or ``configure``, never dropped.

Consequences
************

1. One schema is authored, versioned, validated and tested; a new use case is a
   declaration, not another engine integration.
2. Backends stay small: capabilities, a renderer, document mapping and
   transport.
3. Unsupported capabilities fail at registration or startup, before data is
   indexed.
4. Adding Algolia remains additive: capability flags plus a renderer.

Alternatives Considered
***********************

**Narrow backend contract first.** The initial contribution (``#3``/``#4``)
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
