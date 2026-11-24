"""Tests for the shared HTTP transport."""

import io
import json
from urllib.error import HTTPError

import pytest

from openedx_search.transport import HTTPTransport, TransportError


class _Response(io.BytesIO):
    """A response body used as a context manager."""

    def __enter__(self):
        """Return the body."""
        return self

    def __exit__(self, *exc_info):
        """Close the body and propagate nothing."""
        self.close()
        return False


class _Opener:
    """A stand-in for urllib's opener that replays one outcome."""

    def __init__(self, outcome):
        """Store the outcome and start recording requests."""
        self.outcome = outcome
        self.requests = []

    def open(self, request, timeout=None):  # pylint: disable=unused-argument
        """Record the request and return or raise the configured outcome."""
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return _Response(self.outcome)


def _transport(monkeypatch, outcome, **kwargs):
    """Build a transport whose opener replays ``outcome``."""
    opener = _Opener(outcome)
    monkeypatch.setattr("openedx_search.transport.build_opener", lambda *args: opener)
    transport = HTTPTransport(
        "http://engine:7700",
        {"Authorization": "Bearer secret"},
        **kwargs,
    )
    return transport, opener


def test_request_sends_json_and_decodes_the_response(monkeypatch):
    """The method, URL, headers and body reach the engine; JSON comes back."""
    transport, opener = _transport(monkeypatch, json.dumps({"taskUid": 3}).encode())

    assert transport.request("POST", "/indexes", body={"uid": "course"}) == {"taskUid": 3}

    request = opener.requests[0]
    assert request.full_url == "http://engine:7700/indexes"
    assert request.get_method() == "POST"
    assert request.headers["Authorization"] == "Bearer secret"
    assert json.loads(request.data) == {"uid": "course"}


def test_request_without_a_body_returns_none(monkeypatch):
    """An empty response means the engine returned no payload."""
    transport, opener = _transport(monkeypatch, b"")

    assert transport.request("DELETE", "/indexes/course") is None
    assert opener.requests[0].data is None


def test_missing_resource_is_reported_as_not_found(monkeypatch):
    """A 404 is distinguishable from every other transport failure."""
    error = HTTPError("http://engine/indexes/x", 404, "Not Found", {}, io.BytesIO(b""))
    transport, _ = _transport(monkeypatch, error)

    with pytest.raises(TransportError) as excinfo:
        transport.request("GET", "/indexes/x")

    assert excinfo.value.status == 404
    assert excinfo.value.not_found
    assert not excinfo.value.retryable


def test_server_error_is_retryable(monkeypatch):
    """A 5xx may succeed when retried unchanged."""
    error = HTTPError("http://engine/x", 503, "Unavailable", {}, io.BytesIO(b""))
    transport, _ = _transport(monkeypatch, error)

    with pytest.raises(TransportError) as excinfo:
        transport.request("GET", "/indexes/x")

    assert excinfo.value.status == 503
    assert excinfo.value.retryable


def test_response_over_the_limit_is_rejected(monkeypatch):
    """A response larger than the configured bound is not read in full."""
    transport, _ = _transport(monkeypatch, b"0123456789", max_response_bytes=4)

    with pytest.raises(TransportError, match="size limit"):
        transport.request("GET", "/indexes")


def test_invalid_json_is_rejected(monkeypatch):
    """A body that is not JSON is a transport failure, not a result."""
    transport, _ = _transport(monkeypatch, b"<html>oops</html>")

    with pytest.raises(TransportError, match="invalid JSON"):
        transport.request("GET", "/indexes")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://engine:7700",
        "http://user:pass@engine:7700",
        "http://engine:7700?query=1",
        "http://engine:7700#fragment",
    ],
)
def test_url_must_be_a_plain_http_endpoint(monkeypatch, url):
    """Credentials, a query or a fragment in the URL are rejected up front."""
    monkeypatch.setattr("openedx_search.transport.build_opener", lambda *args: _Opener(b"{}"))

    with pytest.raises(ValueError):
        HTTPTransport(url, {"Authorization": "Bearer secret"})
