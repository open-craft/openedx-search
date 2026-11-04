"""
Backend selection for openedx-search.

A backend is the deployment's named engine connection; an adapter is the
library's implementation for that engine. Reads the deployment configuration
described in ADR 0002. The library ships no default engine: an unconfigured
deployment raises instead of picking one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

__all__ = [
    "BackendConfig",
    "BackendNotConfigured",
    "BackendNotFound",
    "get_backend_config",
]


class BackendNotConfigured(ImproperlyConfigured):
    """No usable backend configuration exists."""


class BackendNotFound(ImproperlyConfigured):
    """The selected backend name is not declared."""


@dataclass(frozen=True)
class BackendConfig:
    """
    One resolved backend: a named engine connection and its options.

    ``search_api_key`` is the parent key for minting scoped search credentials.
    Typesense requires it separately from ``api_key``: the parent of a scoped key
    must allow only ``documents:search``, so the write key cannot mint one.
    Both keys are excluded from ``repr`` so credentials do not leak into logs.
    """

    name: str
    engine: str
    search_api_key: str | None = field(default=None, repr=False)
    options: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Validate the search key and freeze the options mapping."""
        if self.search_api_key is not None:
            if not isinstance(self.search_api_key, str) or not self.search_api_key:
                raise ValueError("search_api_key must be a non-empty string")
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))


def get_backend_config(use_case: str | None = None) -> BackendConfig:
    """
    Resolve the backend for a use case.

    Resolution order: the per-use-case override, then the deployment default.
    Raises ``BackendNotConfigured`` when neither is set, and ``BackendNotFound``
    when the selected name is missing from ``OPENEDX_SEARCH_BACKENDS``.
    """
    backends = getattr(settings, "OPENEDX_SEARCH_BACKENDS", {}) or {}
    if not isinstance(backends, Mapping):
        raise BackendNotConfigured("OPENEDX_SEARCH_BACKENDS must be a mapping")

    overrides = getattr(settings, "OPENEDX_SEARCH_USE_CASE_BACKENDS", {}) or {}
    if not isinstance(overrides, Mapping):
        raise BackendNotConfigured("OPENEDX_SEARCH_USE_CASE_BACKENDS must be a mapping")

    name = overrides.get(use_case) if use_case else None
    if not name:
        name = getattr(settings, "OPENEDX_SEARCH_DEFAULT_BACKEND", None)
    if not name:
        raise BackendNotConfigured(
            "No search backend is configured. Set OPENEDX_SEARCH_DEFAULT_BACKEND "
            "and declare it in OPENEDX_SEARCH_BACKENDS."
        )

    if name not in backends:
        raise BackendNotFound(f"Search backend {name!r} is not declared in OPENEDX_SEARCH_BACKENDS")

    declared = backends[name]
    if not isinstance(declared, Mapping):
        raise BackendNotFound(f"Search backend {name!r} must be a mapping")

    options = dict(declared)
    engine = options.pop("engine", None)
    if not isinstance(engine, str) or not engine:
        raise BackendNotFound(f"Search backend {name!r} has no engine")

    search_api_key = options.pop("search_api_key", None)
    if search_api_key is not None and (not isinstance(search_api_key, str) or not search_api_key):
        raise BackendNotFound(f"Search backend {name!r} has an invalid search_api_key")

    return BackendConfig(
        name=name,
        engine=engine,
        search_api_key=search_api_key,
        options=options,
    )
