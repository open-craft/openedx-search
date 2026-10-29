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
        "default": {"engine": "meilisearch", "url": "http://...", "api_key": "..."},
        "typesense": {"engine": "typesense", "url": "http://...", "api_key": "..."},
    }
    OPENEDX_SEARCH_DEFAULT_BACKEND = "default"
    OPENEDX_SEARCH_USE_CASE_BACKENDS = {"forum": "typesense"}  # optional

``get_backend_config(use_case)`` resolves the override first, then the default,
and raises ``BackendNotConfigured`` when neither is set. The library ships no
default engine on purpose.

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
``ready()`` calls in tests. Each process loads its own in-memory registry; there
is no shared or persisted state.
