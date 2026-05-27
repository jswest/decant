from __future__ import annotations

import httpx
import pytest

from decant.search import (
    BRAVE_ENDPOINT,
    SearchBadQueryError,
    SearchRateLimitedError,
    SearchUnauthorizedError,
    SearchUnavailableError,
    search,
    validate_query,
)


def _brave_response_two_results() -> dict:
    return {
        "grounding": {
            "generic": [
                {
                    "url": "https://example.com/a",
                    "title": "Article A",
                    "snippets": ["snip 1", "snip 2"],
                },
                {
                    "url": "https://example.com/b",
                    "title": "Article B",
                    "snippets": ["snip 3"],
                },
            ]
        },
        "sources": {
            "https://example.com/a": {
                "hostname": "example.com",
                "age": "2025-11-03",
                "title": "Article A",
            },
            "https://example.com/b": {
                "hostname": "example.com",
                "age": None,
                "title": "Article B",
            },
        },
    }


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- Client-side validation --------------------------------------------------


def test_validate_query_rejects_empty():
    with pytest.raises(SearchBadQueryError):
        validate_query("")


def test_validate_query_rejects_too_long():
    with pytest.raises(SearchBadQueryError):
        validate_query("a" * 401)


def test_validate_query_rejects_too_many_words():
    with pytest.raises(SearchBadQueryError):
        validate_query(" ".join(["word"] * 51))


def test_validate_query_accepts_boundary_400_chars():
    validate_query("a" * 400)  # no raise


def test_validate_query_accepts_boundary_50_words():
    validate_query(" ".join(["w"] * 50))  # no raise


@pytest.mark.asyncio
async def test_search_does_not_call_brave_for_bad_query():
    """Client-side validation short-circuits before any HTTP call."""
    called = False

    def handler(req):
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    async with _client(handler) as client:
        with pytest.raises(SearchBadQueryError):
            await search(
                client,
                "key",
                "",
                top=10,
                token_budget=4096,
                freshness=None,
                country="us",
                lang="en",
            )
    assert called is False


# --- Happy path --------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_happy_path_flattens_response():
    def handler(req):
        return httpx.Response(200, json=_brave_response_two_results())

    async with _client(handler) as client:
        out, ms = await search(
            client,
            "key",
            "qwen3 license",
            top=10,
            token_budget=4096,
            freshness=None,
            country="us",
            lang="en",
        )

    assert ms >= 0
    assert [r["url"] for r in out["results"]] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert out["results"][0]["snippets"] == ["snip 1", "snip 2"]
    assert out["results"][0]["age"] == "2025-11-03"
    assert out["results"][1]["age"] is None
    assert out["results"][0]["hostname"] == "example.com"


@pytest.mark.asyncio
async def test_search_sends_subscription_header_and_params():
    captured: dict = {}

    def handler(req):
        captured["headers"] = dict(req.headers)
        captured["url"] = str(req.url)
        return httpx.Response(200, json=_brave_response_two_results())

    async with _client(handler) as client:
        await search(
            client,
            "brv-xyz",
            "qwen3",
            top=5,
            token_budget=2048,
            freshness="pw",
            country="uk",
            lang="fr",
        )

    assert captured["headers"]["x-subscription-token"] == "brv-xyz"
    assert captured["url"].startswith(BRAVE_ENDPOINT + "?")
    assert "q=qwen3" in captured["url"]
    assert "maximum_number_of_urls=5" in captured["url"]
    assert "maximum_number_of_tokens=2048" in captured["url"]
    assert "freshness=pw" in captured["url"]
    assert "country=uk" in captured["url"]
    assert "lang=fr" in captured["url"]


@pytest.mark.asyncio
async def test_search_omits_freshness_when_none():
    captured: dict = {}

    def handler(req):
        captured["url"] = str(req.url)
        return httpx.Response(200, json=_brave_response_two_results())

    async with _client(handler) as client:
        await search(
            client,
            "k",
            "q",
            top=10,
            token_budget=4096,
            freshness=None,
            country="us",
            lang="en",
        )
    assert "freshness" not in captured["url"]


@pytest.mark.asyncio
async def test_search_empty_brave_response_yields_empty_results():
    """Degenerate body (no grounding, no sources) → empty results list, not a crash."""
    async with _client(lambda r: httpx.Response(200, json={})) as client:
        out, _ = await search(
            client, "k", "q",
            top=10, token_budget=4096, freshness=None, country="us", lang="en",
        )
    assert out == {"results": []}


@pytest.mark.asyncio
async def test_search_drops_items_missing_url():
    body = {
        "grounding": {"generic": [{"title": "no-url-here", "snippets": ["s"]}]},
        "sources": {},
    }
    async with _client(lambda r: httpx.Response(200, json=body)) as client:
        out, _ = await search(
            client, "k", "q",
            top=10, token_budget=4096, freshness=None, country="us", lang="en",
        )
    assert out["results"] == []


@pytest.mark.asyncio
async def test_search_picks_iso_date_from_braves_age_list():
    """Brave returns `age` as a list of strings (human, ISO, relative). Pick the ISO."""
    body = {
        "grounding": {
            "generic": [{"url": "https://x.com/a", "title": "A", "snippets": ["s"]}]
        },
        "sources": {
            "https://x.com/a": {
                "hostname": "x.com",
                "age": ["Saturday, July 26, 2025", "2025-07-26", "305 days ago"],
            }
        },
    }
    async with _client(lambda r: httpx.Response(200, json=body)) as client:
        out, _ = await search(
            client, "k", "q",
            top=10, token_budget=4096, freshness=None, country="us", lang="en",
        )
    assert out["results"][0]["age"] == "2025-07-26"


@pytest.mark.asyncio
async def test_search_age_empty_list_becomes_none():
    body = {
        "grounding": {
            "generic": [{"url": "https://x.com/a", "title": "A", "snippets": ["s"]}]
        },
        "sources": {"https://x.com/a": {"hostname": "x.com", "age": []}},
    }
    async with _client(lambda r: httpx.Response(200, json=body)) as client:
        out, _ = await search(
            client, "k", "q",
            top=10, token_budget=4096, freshness=None, country="us", lang="en",
        )
    assert out["results"][0]["age"] is None


@pytest.mark.asyncio
async def test_search_age_picks_first_iso_when_multiple():
    """Documents the picking policy: first ISO-shaped entry wins."""
    body = {
        "grounding": {
            "generic": [{"url": "https://x.com/a", "title": "A", "snippets": ["s"]}]
        },
        "sources": {
            "https://x.com/a": {
                "hostname": "x.com",
                "age": ["2025-07-26", "2026-01-01"],  # both ISO; first wins
            }
        },
    }
    async with _client(lambda r: httpx.Response(200, json=body)) as client:
        out, _ = await search(
            client, "k", "q",
            top=10, token_budget=4096, freshness=None, country="us", lang="en",
        )
    assert out["results"][0]["age"] == "2025-07-26"


@pytest.mark.asyncio
async def test_search_age_list_without_iso_becomes_none():
    body = {
        "grounding": {
            "generic": [{"url": "https://x.com/a", "title": "A", "snippets": ["s"]}]
        },
        "sources": {
            "https://x.com/a": {
                "hostname": "x.com",
                "age": ["Yesterday", "1 day ago"],  # no ISO entry
            }
        },
    }
    async with _client(lambda r: httpx.Response(200, json=body)) as client:
        out, _ = await search(
            client, "k", "q",
            top=10, token_budget=4096, freshness=None, country="us", lang="en",
        )
    assert out["results"][0]["age"] is None


@pytest.mark.asyncio
async def test_search_drops_results_without_source_entry():
    """Items in grounding.generic[] whose URL isn't in sources are dropped (POI/maps)."""
    body = {
        "grounding": {
            "generic": [
                {"url": "https://known.com/a", "title": "A", "snippets": ["s"]},
                {"url": "https://orphan.com/b", "title": "B", "snippets": ["s"]},
            ]
        },
        "sources": {
            "https://known.com/a": {"hostname": "known.com", "age": None},
        },
    }

    def handler(req):
        return httpx.Response(200, json=body)

    async with _client(handler) as client:
        out, _ = await search(
            client,
            "k",
            "q",
            top=10,
            token_budget=4096,
            freshness=None,
            country="us",
            lang="en",
        )
    assert [r["url"] for r in out["results"]] == ["https://known.com/a"]


# --- Error mapping -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_search_unauthorized(status):
    async with _client(lambda r: httpx.Response(status, text="nope")) as client:
        with pytest.raises(SearchUnauthorizedError):
            await search(
                client,
                "k",
                "q",
                top=10,
                token_budget=4096,
                freshness=None,
                country="us",
                lang="en",
            )


@pytest.mark.asyncio
async def test_search_rate_limited_parses_retry_after():
    async with _client(
        lambda r: httpx.Response(429, headers={"Retry-After": "2"})
    ) as client:
        with pytest.raises(SearchRateLimitedError) as exc:
            await search(
                client,
                "k",
                "q",
                top=10,
                token_budget=4096,
                freshness=None,
                country="us",
                lang="en",
            )
    assert exc.value.retry_after == 2.0


@pytest.mark.asyncio
async def test_search_rate_limited_handles_missing_retry_after():
    async with _client(lambda r: httpx.Response(429)) as client:
        with pytest.raises(SearchRateLimitedError) as exc:
            await search(
                client,
                "k",
                "q",
                top=10,
                token_budget=4096,
                freshness=None,
                country="us",
                lang="en",
            )
    assert exc.value.retry_after is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 422])
async def test_search_bad_query_from_brave(status):
    async with _client(lambda r: httpx.Response(status, text="bad")) as client:
        with pytest.raises(SearchBadQueryError):
            await search(
                client,
                "k",
                "q",
                top=10,
                token_budget=4096,
                freshness=None,
                country="us",
                lang="en",
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503])
async def test_search_5xx_is_unavailable(status):
    async with _client(lambda r: httpx.Response(status, text="oops")) as client:
        with pytest.raises(SearchUnavailableError):
            await search(
                client,
                "k",
                "q",
                top=10,
                token_budget=4096,
                freshness=None,
                country="us",
                lang="en",
            )


@pytest.mark.asyncio
async def test_search_connect_error_is_unavailable():
    def handler(req):
        raise httpx.ConnectError("nope")

    async with _client(handler) as client:
        with pytest.raises(SearchUnavailableError):
            await search(
                client,
                "k",
                "q",
                top=10,
                token_budget=4096,
                freshness=None,
                country="us",
                lang="en",
            )


@pytest.mark.asyncio
async def test_search_timeout_is_unavailable():
    def handler(req):
        raise httpx.ReadTimeout("slow")

    async with _client(handler) as client:
        with pytest.raises(SearchUnavailableError):
            await search(
                client,
                "k",
                "q",
                top=10,
                token_budget=4096,
                freshness=None,
                country="us",
                lang="en",
            )
