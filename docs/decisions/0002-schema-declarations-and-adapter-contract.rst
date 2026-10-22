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

Adapters render ``IndexSchema`` in ``configure(schema)``. There is no second
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

Each adapter implements:

::

    validate_schema(schema)           -> None or raises UnsupportedSchema
    configure(schema)                 -> renders and applies settings
    upsert(documents)                 -> WriteReceipt
    search(query)                     -> SearchResult
    mint_credential(scope, expires)   -> str
    reconcile(schema)                 -> ReconcileResult

Each adapter implements ``validate_schema(schema)``. For the Meilisearch and
Typesense adapters it is currently a no-op: both support every schema the library can
express. It is the hook for an adapter with a narrower feature set, which raises
``UnsupportedSchema`` naming the parts it cannot represent. It runs at
registration and before settings are applied; a use case that needs an
unsupported capability fails there, not silently.

For the two implemented adapters the method is a no-op:

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

Reconciliation is versioned and runs on every deploy; the adapter owns its
mechanics (Meilisearch PATCH and reindex, Typesense new collection plus alias).
Lossy edges are reported by ``validate_schema`` or ``configure``, never dropped.

Consequences
************

1. One schema is authored, versioned, validated and tested; a new use case is a
   declaration, not another engine integration.
2. Adapters stay small: capabilities, a renderer, document mapping and
   transport.
3. Unsupported capabilities fail at registration or startup, before data is
   indexed.
4. Adding Algolia remains additive: capability flags plus a renderer.

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
