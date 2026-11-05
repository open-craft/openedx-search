"""Tests for adapter registration and resolution."""

import pytest
from django.test import override_settings

from openedx_search.adapters import AdapterNotRegistered, clear_adapter_registry, get_adapter, register_adapter
from openedx_search.config import BackendConfig
from tests.fakes import SIMPLE_REGISTRATION, FakeAdapter

FAKE_BACKENDS = {
    "default": {"engine": "fake", "url": "http://fake", "api_key": "secret"},
}


def test_get_adapter_builds_the_backend_adapter():
    """Resolution reads the deployment backend and instantiates its factory."""
    register_adapter("fake", FakeAdapter)
    with override_settings(
        OPENEDX_SEARCH_BACKENDS=FAKE_BACKENDS,
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        adapter = get_adapter(SIMPLE_REGISTRATION)

    assert isinstance(adapter, FakeAdapter)
    assert adapter.backend.engine == "fake"
    assert adapter.index == "simple_index"


def test_get_adapter_uses_the_use_case_override():
    """A per-use-case backend wins over the default."""
    register_adapter("fake", FakeAdapter)
    with override_settings(
        OPENEDX_SEARCH_BACKENDS={
            "default": {"engine": "missing", "url": "http://default", "api_key": "a"},
            "fake": {"engine": "fake", "url": "http://fake", "api_key": "b"},
        },
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
        OPENEDX_SEARCH_USE_CASE_BACKENDS={"simple": "fake"},
    ):
        adapter = get_adapter(SIMPLE_REGISTRATION)

    assert adapter.backend.name == "fake"


def test_unregistered_engine_raises():
    """A configured engine without a factory fails loudly."""
    register_adapter("other", FakeAdapter)
    with override_settings(
        OPENEDX_SEARCH_BACKENDS=FAKE_BACKENDS,
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        with pytest.raises(AdapterNotRegistered, match="fake"):
            get_adapter(SIMPLE_REGISTRATION)


def test_duplicate_registration_raises():
    """An engine has one adapter unless replacement is explicit."""
    register_adapter("fake", FakeAdapter)
    with pytest.raises(ValueError, match="already registered"):
        register_adapter("fake", FakeAdapter)


def test_replacement_is_explicit():
    """The test helper can replace a factory on purpose."""
    register_adapter("fake", FakeAdapter)
    register_adapter("fake", FakeAdapter, replace=True)
    assert isinstance(get_adapter(SIMPLE_REGISTRATION, backend=_fake_backend()), FakeAdapter)


def test_invalid_registration_inputs_raise():
    """The engine and the factory are validated."""
    with pytest.raises(ValueError, match="non-empty"):
        register_adapter("", FakeAdapter)
    with pytest.raises(ValueError, match="callable"):
        register_adapter("fake", "not callable")


def test_clear_adapter_registry():
    """Clearing removes every factory."""
    register_adapter("fake", FakeAdapter)
    clear_adapter_registry()
    with override_settings(
        OPENEDX_SEARCH_BACKENDS=FAKE_BACKENDS,
        OPENEDX_SEARCH_DEFAULT_BACKEND="default",
    ):
        with pytest.raises(AdapterNotRegistered):
            get_adapter(SIMPLE_REGISTRATION)


def _fake_backend():
    """Build a backend without touching Django settings."""
    return BackendConfig(name="fake", engine="fake", url="http://fake", api_key="secret")
