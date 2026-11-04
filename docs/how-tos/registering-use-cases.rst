Registering a use case
######################

The registration API is engine-neutral. A use case declares its logical index
and schema; the deployment chooses the backend.

Studio example
**************

The course and library schemas used to validate the model and registration API
live in ``tests/fixtures.py``:

.. literalinclude:: ../../tests/fixtures.py
   :language: python

Registering it
**************

.. code-block:: python

    from openedx_search.registry import get_registration, register_use_case
    from myapp.search import STUDIO_LIBRARY

    register_use_case(STUDIO_LIBRARY)
    print(get_registration("studio_library").index)

Backend selection
*****************

Deployments choose the engine in configuration, not in the registration:

.. code-block:: python

    OPENEDX_SEARCH_BACKENDS = {
        "default": {
            "engine": "meilisearch",
            "url": "http://...",
            "public_url": "https://...",
            "api_key": "...",
        },
        "typesense": {
            "engine": "typesense",
            "url": "http://...",
            "public_url": "https://...",
            "api_key": "...",
            "search_api_key": "...",
        },
    }
    OPENEDX_SEARCH_DEFAULT_BACKEND = "default"
    OPENEDX_SEARCH_USE_CASE_BACKENDS = {"forum": "typesense"}  # optional

``get_backend_config(use_case)`` resolves the override first, then the default,
and raises ``BackendNotConfigured`` when neither is set. The library ships no
default engine on purpose. ``search_api_key`` is the parent key for scoped
search credentials; Typesense requires it because the parent of a scoped key must
allow only ``documents:search``, so ``api_key`` cannot mint one. ``url`` is the
connection URL, while ``public_url`` is the one the browser uses and defaults to
``url`` when unset. The entry is typed: ``engine``, ``url`` and ``api_key`` are
required, ``search_api_key`` and ``public_url`` are optional, and any other key is
rejected, so a typo fails at resolution.

Loading registrations
*********************

Registrations are consumer-owned. Call ``register_use_case`` from the consumer's
``AppConfig.ready()``:

.. code-block:: python

    from django.apps import AppConfig

    from openedx_search.registry import register_use_case
    from myapp.search import STUDIO_LIBRARY


    class SearchConfig(AppConfig):
        name = "my_app"

        def ready(self):
            register_use_case(STUDIO_LIBRARY, replace=True)

``replace=True`` makes the hook safe under development reloads and repeated
``ready()`` calls in tests. Each process loads its own in-memory registry; the
registry has no shared or persisted state. The rebuild target is the exception:
it is stored in Redis so every writer process sees it.

Registering validates the declaration only; it does not resolve a backend.
Schema compatibility with the configured adapter is checked by a Django system
check when ``manage.py check`` runs, and by the deploy-time commands that run
checks (``migrate`` among them), not by ``register_use_case``.

Reindexing and schema migrations
********************************

There are two rebuilds, and the registration version decides which one is safe.

**Reindex, same version.** The live index and the target accept the same
documents, so ``upsert`` and ``delete`` dual-write while the rebuild runs. No
writer stops, and the swap is invisible to readers. Use it for a data repair, a
source change, or a settings change that needs a full reindex.

**Migration, new version.** The live index and the target cannot accept each
other's documents, so writes stop for the whole operation:

.. code-block:: bash

    # 1. stop the indexing workers; Celery finishes in-flight tasks on SIGTERM
    kubectl scale deployment/cms-worker --replicas=0
    kubectl wait --for=delete pod -l app.kubernetes.io/component=cms-worker --timeout=15m

    # 2. deploy the new code, and stop synchronous writers as well

    # 3. rebuild and swap with the new image; the last argument names this run
    kubectl create job --from=cronjob/openedx-search-rebuild rebuild-v2-$(date +%Y%m%d)

    # 4. resume
    kubectl scale deployment/cms-worker --replicas=1

Stop writers before deploying: a new writer matches the target version and would
dual-write against the old live index. If an old writer survives the pause,
``upsert`` and ``delete`` raise ``SchemaVersionMismatch`` instead of writing an
incompatible document. A task that hits it retries until the migration finishes:

.. code-block:: python

    @shared_task(
        bind=True,
        acks_late=True,
        reject_on_worker_lost=True,
        max_retries=20,
    )
    def index_document(self, use_case, key):
        try:
            upsert(use_case, [build_document(key)])
        except SchemaVersionMismatch:
            logger.warning("schema version mismatch indexing %s", key)
            raise self.retry(countdown=300, max_retries=20)

``acks_late`` and ``reject_on_worker_lost`` requeue a task whose worker dies
mid-run; the retry covers the pause.

The rebuild target and lease live in the cache selected by
``OPENEDX_SEARCH_CACHE_ALIAS`` (default ``default``). Point it at a shared Redis
cache, not the platform's memcached default: an eviction aborts the rebuild
rather than corrupting it.

Indexing tasks should carry a document key and re-read the document when they
run, so a task enqueued before the pause writes the new shape when it is
retried. Give the pods a termination grace period longer than the slowest task,
so the drain does not drop in-flight work.

Reads keep serving the old index during the migration. If the read path depends
on the new shape, extend the window to reads as well.
