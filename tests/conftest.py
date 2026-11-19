"""Shared pytest fixtures."""

import pytest

from openedx_search.registry import clear_registry


@pytest.fixture(autouse=True)
def isolated_registry():
    """Give every test an empty use-case registry."""
    clear_registry()
    yield
    clear_registry()
