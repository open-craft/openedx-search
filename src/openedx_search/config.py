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

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

__all__ = [
    "BackendConfig",
    "BackendNotConfigured",
    "BackendNotFound",
    "get_backend_config",
]

KNOWN_KEYS = frozenset({"engine", "url", "api_key", "search_api_key", "public_url"})


class BackendNotConfigured(ImproperlyConfigured):
    """No usable backend configuration exists."""


class BackendNotFound(ImproperlyConfigured):
    """The selected backend is missing or invalid."""


@dataclass(frozen=True)
class BackendConfig:
    """
    One resolved backend: a named engine connection.

    ``url`` is the connection URL and ``api_key`` the write key.
    ``search_api_key`` is the parent key for minting scoped search credentials.
    Typesense requires it separately: the parent of a scoped key must allow only
    ``documents:search``, so the write key cannot mint one. Both keys are
    excluded from ``repr`` so credentials do not leak into logs.

    ``public_url`` is the address the browser uses. It can differ from the
    connection URL when the engine is internal but proxied or TLS-terminated,
    and defaults to that URL when unset.
    """

    name: str
    engine: str
    url: str
    api_key: str = field(repr=False)
    search_api_key: str | None = field(default=None, repr=False)
    public_url: str | None = None

    def __post_init__(self) -> None:
        """Validate the connection settings."""
        for attribute in ("engine", "url", "api_key"):
            value = getattr(self, attribute)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{attribute} must be a non-empty string")
        if self.search_api_key is not None:
            if not isinstance(self.search_api_key, str) or not self.search_api_key:
                raise ValueError("search_api_key must be a non-empty string")
        if self.public_url is not None:
            if not isinstance(self.public_url, str) or not self.public_url:
                raise ValueError("public_url must be a non-empty string")


def get_backend_config(use_case: str | None = None) -> BackendConfig:
    """
    Resolve the backend for a use case.

    Resolution order: the per-use-case override, then the deployment default.
    Raises ``BackendNotConfigured`` when neither is set, and ``BackendNotFound``
    when the selected entry is missing, incomplete, or has unknown keys.
    """
    backends = getattr(settings, "OPENEDX_SEARCH_BACKENDS", {}) or {}
    if not isinstance(backends, Mapping):
        raise BackendNotConfigured("OPENEDX_SEARCH_BACKENDS must be a mapping")

    overrides = getattr(settings, "OPENEDX_SEARCH_USE_CASE_BACKENDS", {}) or {}
    if not isinstance(overrides, Mapping):
        raise BackendNotConfigured("OPENEDX_SEARCH_USE_CASE_BACKENDS must be a mapping")

    override = overrides.get(use_case) if use_case else None
    if override is not None and not isinstance(override, str):
        raise BackendNotFound(
            f"Search backend override for {use_case!r} must be a backend name"
        )

    default_name = getattr(settings, "OPENEDX_SEARCH_DEFAULT_BACKEND", None)
    if default_name is not None and not isinstance(default_name, str):
        raise BackendNotFound("OPENEDX_SEARCH_DEFAULT_BACKEND must be a backend name")

    name = override or default_name
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

    unknown = set(declared) - KNOWN_KEYS
    if unknown:
        raise BackendNotFound(
            f"Search backend {name!r} has unknown keys: {', '.join(sorted(unknown))}"
        )

    engine = declared.get("engine")
    url = declared.get("url")
    api_key = declared.get("api_key")
    search_api_key = declared.get("search_api_key")
    public_url = declared.get("public_url")
    if public_url is None:
        public_url = url

    try:
        return BackendConfig(
            name=name,
            engine=engine,
            url=url,
            api_key=api_key,
            search_api_key=search_api_key,
            public_url=public_url,
        )
    except ValueError as exc:
        raise BackendNotFound(f"Search backend {name!r}: {exc}") from exc
