from __future__ import annotations

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from decant.fetch import (
    FetchFailedError,
    FetchResult,
    Fetcher,
    _HTTPStatus,
    _is_retryable,
    _with_retry,
)


# --- Retry policy unit tests (no Playwright) ---------------------------------


def test_is_retryable():
    assert _is_retryable(500) is True
    assert _is_retryable(503) is True
    assert _is_retryable(429) is True
    assert _is_retryable(404) is False
    assert _is_retryable(200) is False


@pytest.mark.asyncio
async def test_with_retry_success_first_try():
    calls = 0

    async def attempt():
        nonlocal calls
        calls += 1
        return "ok"

    assert await _with_retry(attempt, backoff_s=0) == "ok"
    assert calls == 1


@pytest.mark.asyncio
async def test_with_retry_recovers_on_second_attempt():
    calls = 0

    async def attempt():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _HTTPStatus(503)
        return "ok"

    assert await _with_retry(attempt, backoff_s=0) == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_with_retry_no_retry_on_4xx_non_429():
    calls = 0

    async def attempt():
        nonlocal calls
        calls += 1
        raise _HTTPStatus(404)

    with pytest.raises(FetchFailedError, match="HTTP 404"):
        await _with_retry(attempt, backoff_s=0)
    assert calls == 1


@pytest.mark.asyncio
async def test_with_retry_429_does_retry():
    calls = 0

    async def attempt():
        nonlocal calls
        calls += 1
        raise _HTTPStatus(429)

    with pytest.raises(FetchFailedError, match="retry failed"):
        await _with_retry(attempt, backoff_s=0)
    assert calls == 2


@pytest.mark.asyncio
async def test_with_retry_double_failure_raises():
    async def attempt():
        raise _HTTPStatus(500)

    with pytest.raises(FetchFailedError, match="retry failed"):
        await _with_retry(attempt, backoff_s=0)


@pytest.mark.asyncio
async def test_with_retry_sleeps_default_two_seconds(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr("decant.fetch.asyncio.sleep", fake_sleep)

    async def attempt():
        raise _HTTPStatus(503)

    with pytest.raises(FetchFailedError):
        await _with_retry(attempt)  # default backoff_s=2.0
    assert slept == [2.0]


# --- Route handler unit test (no Playwright launch) --------------------------


class _FakeRoute:
    def __init__(self, resource_type: str) -> None:
        self.request = type("Req", (), {"resource_type": resource_type})()
        self.actions: list[str] = []

    async def abort(self):
        self.actions.append("abort")

    async def continue_(self):
        self.actions.append("continue")


@pytest.mark.asyncio
async def test_route_handler_blocks_configured_resources():
    f = Fetcher(user_agent="x", blocked_resources=["image", "font"])
    img = _FakeRoute("image")
    doc = _FakeRoute("document")
    await f._route(img)
    await f._route(doc)
    assert img.actions == ["abort"]
    assert doc.actions == ["continue"]


def test_fetcher_loads_readability_js():
    f = Fetcher(user_agent="x", blocked_resources=[])
    assert "function Readability" in f._readability_js


# --- End-to-end Playwright test (gated) --------------------------------------


def _serve_once(html: str) -> tuple[str, threading.Thread, HTTPServer]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}/", thread, server


@pytest.mark.skipif(
    os.environ.get("DECANT_E2E") != "1",
    reason="set DECANT_E2E=1 to run (requires `playwright install chromium`)",
)
@pytest.mark.asyncio
async def test_fetcher_end_to_end():
    html = "<html><head><title>Hi</title></head><body><h1>Hello</h1></body></html>"
    url, _thread, server = _serve_once(html)
    try:
        async with Fetcher(user_agent="Decant/test", blocked_resources=[]) as f:
            r = await f.fetch(url, timeout_s=10)
        assert isinstance(r, FetchResult)
        assert r.title == "Hi"
        assert "Hello" in r.html
        # article may be None for a single-h1 page; just confirm the field exists.
        assert r.article is None or isinstance(r.article, dict)
    finally:
        server.shutdown()


@pytest.mark.skipif(
    os.environ.get("DECANT_E2E") != "1",
    reason="set DECANT_E2E=1 to run (requires `playwright install chromium`)",
)
@pytest.mark.asyncio
async def test_fetcher_captures_readability_article():
    """A page with real article content should yield a parsed `article` dict."""
    html = """
    <html>
    <head><title>An Article</title></head>
    <body>
    <article>
    <h1>The Title</h1>
    <p>This is a meaningful first paragraph with enough content for Readability
    to consider it an article. Readability looks for paragraph density and
    meaningful prose, so we need a few sentences here to pass its heuristics.</p>
    <p>A second paragraph adds more content to ensure the article gets parsed
    correctly. Without sufficient content, Readability.js will return null
    rather than guess.</p>
    <p>And a third paragraph to be safe — the heuristic threshold scales with
    the surrounding noise on the page.</p>
    </article>
    </body>
    </html>
    """
    url, _thread, server = _serve_once(html)
    try:
        async with Fetcher(user_agent="Decant/test", blocked_resources=[]) as f:
            r = await f.fetch(url, timeout_s=10)
        assert r.article is not None
        assert "meaningful first paragraph" in r.article["textContent"]
        assert r.article["length"] >= 250
    finally:
        server.shutdown()
