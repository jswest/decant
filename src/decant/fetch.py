"""Playwright orchestration.

A single `Fetcher` is created per `decant url` invocation and reused across
all URLs in the batch — one Chromium process, one browser context. This
saves ~1s per URL vs. relaunching, and a fresh context per run means
cookies never persist across invocations (spec §"Polite scraping policy").
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from playwright.async_api import (
    Browser,
    BrowserContext,
    Error as PlaywrightError,
    Route,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)


class FetchFailedError(Exception):
    """Playwright threw, navigation hit a hard error, or response was 4xx/5xx."""


class _HTTPStatus(Exception):
    """Internal sentinel for status-code-driven retry decisions."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status


@dataclass
class FetchResult:
    final_url: str
    title: str
    html: str
    elapsed_ms: int


def _is_retryable(status: int) -> bool:
    return status >= 500 or status == 429


async def _with_retry(attempt, *, backoff_s: float = 2.0):
    """Call `attempt` once; on a retryable HTTP status, sleep and retry once."""
    try:
        return await attempt()
    except _HTTPStatus as e:
        if not _is_retryable(e.status):
            raise FetchFailedError(f"HTTP {e.status}") from e
        await asyncio.sleep(backoff_s)
        try:
            return await attempt()
        except (_HTTPStatus, PlaywrightError) as e2:
            raise FetchFailedError(f"retry failed: {e2}") from e2


class Fetcher:
    def __init__(self, user_agent: str, blocked_resources: list[str]) -> None:
        self._user_agent = user_agent
        self._blocked = set(blocked_resources)
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> Fetcher:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True)
        self._context = await self._browser.new_context(user_agent=self._user_agent)
        await self._context.route("**/*", self._route)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def _route(self, route: Route) -> None:
        if route.request.resource_type in self._blocked:
            await route.abort()
        else:
            await route.continue_()

    async def fetch(
        self, url: str, timeout_s: int, navigation_wait: str = "networkidle"
    ) -> FetchResult:
        """Navigate to `url` and capture (final_url, title, html).

        Retries once on 5xx and 429 with a 2s backoff; 4xx (other than 429)
        is fatal. Total wall time per attempt is bounded by `timeout_s`.
        """
        assert self._context is not None, "use Fetcher as an async context manager"
        try:
            return await _with_retry(
                lambda: self._attempt(url, timeout_s, navigation_wait)
            )
        except PlaywrightError as e:
            raise FetchFailedError(f"playwright error for {url}: {e}") from e

    async def _attempt(
        self, url: str, timeout_s: int, navigation_wait: str
    ) -> FetchResult:
        start = time.monotonic()
        page = await self._context.new_page()
        try:
            response = await page.goto(
                url, wait_until="domcontentloaded", timeout=timeout_s * 1000
            )
            if response is None:
                raise FetchFailedError(f"no response for {url}")
            if response.status >= 400:
                raise _HTTPStatus(response.status)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            remaining_ms = max(0, timeout_s * 1000 - elapsed_ms)
            try:
                await page.wait_for_load_state(navigation_wait, timeout=remaining_ms)
            except PlaywrightTimeoutError:
                pass  # networkidle is best-effort; we already have the DOM
            return FetchResult(
                final_url=page.url,
                title=await page.title(),
                html=await page.content(),
                elapsed_ms=int((time.monotonic() - start) * 1000),
            )
        finally:
            await page.close()
