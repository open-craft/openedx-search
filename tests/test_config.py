"""Tests for backend selection from deployment settings."""

import pytest
from django.test import override_settings

from openedx_search.config import BackendConfig, BackendNotConfigured, BackendNotFound, get_backend_config


def test_no_default_raises():
    """An unconfigured deployment fails instead of picking an engine."""
    with override_settings(OPENEDX_SEARCH_BACKENDS={}, OPENEDX_SEARCH_DEFAULT_BACKEND=None):
        with pytest.raises(BackendNotConfigured):
            get_backend_config()


def test_resolves_default_backend():
    """The deployment default is used when there is no override."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": {"engine": "meilisearch", "url": "http://localhost:7700"}},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        config = get_backend_config()

    assert config.name == "default"
    assert config.engine == "meilisearch"
    assert config.options["url"] == "http://localhost:7700"


def test_use_case_override_wins():
    """A per-use-case backend overrides the deployment default."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={
            "default": {"engine": "meilisearch"},
            "typesense": {"engine": "typesense"},
        },
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
        OPENEDX_SEARCH_USE_CASE_BACKENDS={"forum": "typesense"},
    ):
        assert get_backend_config("forum").engine == "typesense"
        assert get_backend_config("studio_course").engine == "meilisearch"


def test_unknown_backend_raises():
    """The selected name must exist in the declared backends."""
    with override_settings(OPENEDX_SEARCH_BACKENDS={}, OPENEDX_SEARCH_DEFAULT_BACKEND="missing"):
        with pytest.raises(BackendNotFound):
            get_backend_config()


def test_backend_without_engine_raises():
    """A backend entry needs an engine."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": {"url": "http://localhost"}},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        with pytest.raises(BackendNotFound):
            get_backend_config()


def test_options_are_frozen_and_hidden():
    """Options are read-only and left out of the repr."""
    config = BackendConfig(name="default", engine="meilisearch", options={"api_key": "secret"})
    assert "secret" not in repr(config)
    with pytest.raises(TypeError):
        config.options["api_key"] = "other"


def test_resolves_search_api_key():
    """The search key is a first-class field, not part of the options."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={
            "default": {
                "engine": "typesense",
                "url": "http://localhost:8108",
                "api_key": "write-secret",
                "search_api_key": "search-secret",
            }
        },
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        config = get_backend_config()

    assert config.search_api_key == "search-secret"
    assert "search_api_key" not in config.options
    assert "search-secret" not in repr(config)


def test_search_api_key_is_optional():
    """Meilisearch mints tenant tokens from the write key."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": {"engine": "meilisearch"}},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        assert get_backend_config().search_api_key is None


def test_invalid_search_api_key_raises():
    """An empty search key is a configuration error, not a silent None."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": {"engine": "typesense", "search_api_key": ""}},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        with pytest.raises(BackendNotFound):
            get_backend_config()


def test_non_mapping_backends_raise():
    """The backends setting must be a mapping."""
    with override_settings(OPENEDX_SEARCH_BACKENDS="not-a-mapping", OPENEDX_SEARCH_DEFAULT_BACKEND="default"):
        with pytest.raises(BackendNotConfigured):
            get_backend_config()


def test_non_mapping_use_case_backends_raise():
    """The per-use-case overrides must be a mapping."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": {"engine": "meilisearch"}},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
        OPENEDX_SEARCH_USE_CASE_BACKENDS="not-a-mapping",
    ):
        with pytest.raises(BackendNotConfigured):
            get_backend_config()


def test_non_mapping_backend_entry_raises():
    """Each backend entry must be a mapping."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": "meilisearch"},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        with pytest.raises(BackendNotFound):
            get_backend_config()
