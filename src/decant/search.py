"""Brave Search LLM Context API client.

Single async function — no provider abstraction in v0. Mirrors the
structure of `decant.distill`: httpx-based, custom exception hierarchy
that the CLI maps onto the closed ErrorCode set.
"""

from __future__ import annotations

import time

import httpx

BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/llm/context"
DEFAULT_TIMEOUT_S = 30  # per Brave's guidance


class SearchUnauthorizedError(Exception):
    """Brave returned 401 or 403."""


class SearchRateLimitedError(Exception):
    """Brave returned 429."""

    def __init__(self, retry_after: float | None) -> None:
        super().__init__(f"rate limited (retry_after={retry_after})")
        self.retry_after = retry_after


class SearchUnavailableError(Exception):
    """Network failure, 5xx, timeout."""


class SearchBadQueryError(Exception):
    """Query failed validation client-side or Brave returned 4xx (non-auth, non-rate-limit)."""


def validate_query(query: str) -> None:
    """Brave's `q` constraints: 1–400 chars, ≤50 words."""
    if not 1 <= len(query) <= 400:
        raise SearchBadQueryError(
            f"query must be 1–400 chars, got {len(query)}"
        )
    word_count = len(query.split())
    if word_count > 50:
        raise SearchBadQueryError(f"query must be ≤50 words, got {word_count}")


async def search(
    client: httpx.AsyncClient,
    api_key: str,
    query: str,
    *,
    top: int,
    token_budget: int,
    freshness: str | None,
    country: str,
    lang: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> tuple[dict, int]:
    """Call Brave; return (flattened response dict, elapsed_ms).

    The returned dict has shape::

        {
          "results": [
            {"url", "title", "hostname", "age", "snippets": [...]},
            ...
          ]
        }

    The caller is expected to wrap this with `query`, `fetched_at`,
    and a populated `meta` block.
    """
    validate_query(query)

    params = {
        "q": query,
        "maximum_number_of_urls": top,
        "maximum_number_of_tokens": token_budget,
        "country": country,
        "lang": lang,
    }
    if freshness is not None:
        params["freshness"] = freshness

    headers = {"X-Subscription-Token": api_key, "Accept": "application/json"}

    start = time.monotonic()
    try:
        r = await client.get(
            BRAVE_ENDPOINT, params=params, headers=headers, timeout=timeout_s
        )
    except httpx.TimeoutException as e:
        raise SearchUnavailableError(f"timeout: {e}") from e
    except httpx.HTTPError as e:
        raise SearchUnavailableError(str(e)) from e

    _raise_for_status(r)

    flattened = _flatten(r.json())
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return flattened, elapsed_ms


def _raise_for_status(r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    if r.status_code in (401, 403):
        raise SearchUnauthorizedError(f"HTTP {r.status_code}: {r.text[:200]}")
    if r.status_code == 429:
        raise SearchRateLimitedError(_parse_retry_after(r.headers.get("Retry-After")))
    if r.status_code in (400, 422):
        raise SearchBadQueryError(f"HTTP {r.status_code}: {r.text[:200]}")
    # 5xx and any other 4xx (e.g. 404 — wrong endpoint) are "unavailable"
    # from a caller's point of view; nothing they can fix at the query level.
    raise SearchUnavailableError(f"HTTP {r.status_code}: {r.text[:200]}")


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None  # Brave sends seconds; HTTP-date form is not parsed in v0


def _flatten(body: dict) -> dict:
    """Collapse Brave's `grounding.generic[]` + `sources{}` into a flat results list.

    Only items whose URL appears in `sources` are emitted (drops POI/map
    entries that don't carry a fetchable URL, per spec scope).
    """
    sources = body.get("sources") or {}
    generic = (body.get("grounding") or {}).get("generic") or []

    results: list[dict] = []
    for item in generic:
        url = item.get("url")
        if not url or url not in sources:
            continue
        src = sources[url]
        results.append(
            {
                "url": url,
                "title": item.get("title") or src.get("title") or "",
                "hostname": src.get("hostname") or "",
                "age": src.get("age"),
                "snippets": item.get("snippets") or [],
            }
        )
    return {"results": results}
