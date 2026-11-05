"""Shared pytest fixtures."""

import pytest

from openedx_search.adapters import clear_adapter_registry
from openedx_search.registry import clear_registry


@pytest.fixture(autouse=True)
def isolated_registries():
    """Give every test empty use-case and adapter registries."""
    clear_registry()
    clear_adapter_registry()
    yield
    clear_registry()
    clear_adapter_registry()
