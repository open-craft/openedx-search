"""
Minimal HTTP transport shared by the engine adapters.

No engine client library is a dependency: the Open edX platform pins its own
requirements, so a client pinned here would drift or conflict. The transport is
deliberately small. It follows no redirects, because an ``Authorization`` header
must not reach another origin; it bounds the response size; and it never puts a
credential or a response body into an exception, where it would reach a log.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .adapters import AdapterError

__all__ = ["HTTPTransport", "TransportError"]

DEFAULT_TIMEOUT = 10.0
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


class TransportError(AdapterError):
    """An engine request failed before it produced a usable response."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        """Store the HTTP status, when the engine answered at all."""
        super().__init__(message)
        self.status = status

    @property
    def not_found(self) -> bool:
        """Whether the engine reported that the resource does not exist."""
        return self.status == 404

    @property
    def retryable(self) -> bool:
        """Whether the caller may retry the request unchanged."""
        return self.status is None or self.status == 429 or self.status >= 500


class _NoRedirect(HTTPRedirectHandler):
    """Refuse redirects so an authorization header cannot be forwarded."""

    # urllib requires the full override signature.
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # pylint: disable=too-many-positional-arguments
        """Never produce a redirected request."""
        return None


class HTTPTransport:
    """JSON requests to one engine endpoint with fixed credentials."""

    def __init__(
        self,
        base_url: str,
        headers: Mapping[str, str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        """Validate the endpoint and freeze the request defaults."""
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.username:
            raise ValueError("engine URL must be an http(s) URL without credentials")
        if parts.query or parts.fragment:
            raise ValueError("engine URL must not carry a query or fragment")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int):
            raise ValueError("max_response_bytes must be a positive integer")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be a positive integer")
        self.base_url = base_url.rstrip("/")
        self.headers = dict(headers)
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self._opener = build_opener(_NoRedirect())

    def request(self, method: str, path: str, body: object = None) -> object:
        """Send one JSON request and return the decoded response, or ``None``."""
        headers = {"Accept": "application/json", **self.headers}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                content = response.read(self.max_response_bytes + 1)
        except HTTPError as error:
            error.close()
            raise TransportError(f"engine returned HTTP {error.code}", status=error.code) from None
        except (URLError, TimeoutError, OSError):
            raise TransportError("engine request failed") from None
        if len(content) > self.max_response_bytes:
            raise TransportError("engine response exceeds the size limit")
        if not content:
            return None
        try:
            return json.loads(content.decode("utf-8"))
        except ValueError:
            raise TransportError("engine returned an invalid JSON response") from None
