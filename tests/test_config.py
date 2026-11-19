"""Tests for backend selection from deployment settings."""

import pytest
from django.test import override_settings

from openedx_search.config import BackendConfig, BackendNotConfigured, BackendNotFound, get_backend_config

MEILISEARCH = {
    "engine": "meilisearch",
    "url": "http://localhost:7700",
    "api_key": "write-secret",
}


def test_no_default_raises():
    """An unconfigured deployment fails instead of picking an engine."""
    with override_settings(OPENEDX_SEARCH_BACKENDS={}, OPENEDX_SEARCH_DEFAULT_BACKEND=None):
        with pytest.raises(BackendNotConfigured):
            get_backend_config()


def test_resolves_default_backend():
    """The deployment default is used when there is no override."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": MEILISEARCH},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        config = get_backend_config()

    assert config.name == "default"
    assert config.engine == "meilisearch"
    assert config.url == "http://localhost:7700"
    assert config.api_key == "write-secret"


def test_use_case_override_wins():
    """A per-use-case backend overrides the deployment default."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={
            "default": MEILISEARCH,
            "typesense": {
                "engine": "typesense",
                "url": "http://typesense:8108",
                "api_key": "typesense-secret",
            },
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


def test_non_string_use_case_backend_raises():
    """A non-string override is a configuration error, not a TypeError."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": MEILISEARCH},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
        OPENEDX_SEARCH_USE_CASE_BACKENDS={"forum": ["typesense"]},
    ):
        with pytest.raises(BackendNotFound):
            get_backend_config("forum")


def test_non_string_default_backend_raises():
    """A non-string default is a configuration error, not a TypeError."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": MEILISEARCH},
        OPENEDX_SEARCH_DEFAULT_BACKEND=["default"],
    ):
        with pytest.raises(BackendNotFound):
            get_backend_config()


def test_backend_without_engine_raises():
    """A backend entry needs an engine."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": {"url": "http://localhost", "api_key": "secret"}},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        with pytest.raises(BackendNotFound):
            get_backend_config()


def test_backend_without_url_raises():
    """A backend entry needs a connection URL."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": {"engine": "meilisearch", "api_key": "secret"}},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        with pytest.raises(BackendNotFound):
            get_backend_config()


def test_backend_without_api_key_raises():
    """A backend entry needs a write key."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={
            "default": {"engine": "meilisearch", "url": "http://localhost:7700"}
        },
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        with pytest.raises(BackendNotFound):
            get_backend_config()


def test_unknown_backend_key_raises():
    """A typo in a key fails instead of being ignored."""
    entry = dict(MEILISEARCH, **{"api-key": "typo"})
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": entry},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        with pytest.raises(BackendNotFound, match="api-key"):
            get_backend_config()


def test_keys_are_hidden_and_settings_are_frozen():
    """Credentials are left out of the repr and the config is immutable."""
    config = BackendConfig(
        name="default",
        engine="typesense",
        url="http://typesense:8108",
        api_key="write-secret",
        search_api_key="search-secret",
    )

    assert "write-secret" not in repr(config)
    assert "search-secret" not in repr(config)
    assert "typesense:8108" in repr(config)
    with pytest.raises(AttributeError):
        config.api_key = "other"


def test_resolves_search_api_key():
    """The search key is a first-class field."""
    entry = dict(MEILISEARCH, engine="typesense", search_api_key="search-secret")
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": entry},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        config = get_backend_config()

    assert config.search_api_key == "search-secret"
    assert "search-secret" not in repr(config)


def test_search_api_key_is_optional():
    """Meilisearch mints tenant tokens from the write key."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": MEILISEARCH},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        assert get_backend_config().search_api_key is None


def test_invalid_search_api_key_raises():
    """An empty search key is a configuration error, not a silent None."""
    entry = dict(MEILISEARCH, engine="typesense", search_api_key="")
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": entry},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        with pytest.raises(BackendNotFound):
            get_backend_config()


def test_public_url_defaults_to_connection_url():
    """A single-host deployment uses one URL for both."""
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": MEILISEARCH},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        assert get_backend_config().public_url == "http://localhost:7700"


def test_public_url_can_differ_from_connection_url():
    """The browser URL can be the proxied address while the backend uses the internal one."""
    entry = dict(MEILISEARCH, public_url="https://search.example.com")
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": entry},
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        config = get_backend_config()

    assert config.url == "http://localhost:7700"
    assert config.public_url == "https://search.example.com"


def test_invalid_public_url_raises():
    """An empty public URL is a configuration error."""
    entry = dict(MEILISEARCH, public_url="")
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={"default": entry},
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
        OPENEDX_SEARCH_BACKENDS={"default": MEILISEARCH},
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
