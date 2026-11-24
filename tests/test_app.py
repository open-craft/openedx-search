"""
Smoke tests for the openedx_search package and its Django app.
"""

from django.apps import apps
from django.test import override_settings

import openedx_search
from openedx_search.adapters import get_adapter
from openedx_search.meilisearch import MeilisearchAdapter
from tests.fixtures import STUDIO_COURSE


def test_version_is_set():
    """The package exposes the version it was installed with."""
    assert openedx_search.__version__


def test_app_is_installed():
    """The Django app loads under its expected label."""
    app_config = apps.get_app_config("openedx_search")
    assert app_config.verbose_name == "Open edX Search"


def test_app_ready_registers_the_meilisearch_adapter():
    """App initialization makes the shipped engine adapter resolvable."""
    apps.get_app_config("openedx_search").ready()
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={
            "default": {
                "engine": "meilisearch",
                "url": "http://engine:7700",
                "api_key": "secret",
            }
        },
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        adapter = get_adapter(STUDIO_COURSE)

    assert isinstance(adapter, MeilisearchAdapter)
