from __future__ import annotations

import json

import httpx
import pytest

from decant.distill import (
    OllamaBadJsonError,
    OllamaTimeoutError,
    OllamaUnavailableError,
    build_prompt,
    distill,
    truncate,
)


def _valid_chat_response() -> dict:
    return {
        "message": {
            "content": json.dumps(
                {
                    "answer": "Yes.",
                    "page_topic": "A page about things.",
                    "findings": [],
                }
            )
        }
    }


@pytest.mark.asyncio
async def test_distill_happy_path():
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=_valid_chat_response())
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result, ms, trunc = await distill(
            client, "http://x", "qwen3:32b", "Q?", "md", timeout_s=10
        )
    assert result.answer == "Yes."
    assert result.page_topic == "A page about things."
    assert result.findings == []
    assert ms >= 0
    assert trunc is False


@pytest.mark.asyncio
async def test_distill_sends_format_schema():
    captured: dict = {}

    def handler(req):
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, json=_valid_chat_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await distill(client, "http://x", "qwen3:32b", "Q?", "md", timeout_s=10)
    assert captured["body"]["format"]["type"] == "object"
    assert "answer" in captured["body"]["format"]["properties"]
    assert captured["body"]["stream"] is False


@pytest.mark.asyncio
async def test_distill_retries_once_on_bad_json_then_raises():
    calls = 0

    def handler(req):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"message": {"content": "not-json"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OllamaBadJsonError):
            await distill(client, "http://x", "q", "Q?", "md", timeout_s=10)
    assert calls == 2  # one initial attempt + one retry


@pytest.mark.asyncio
async def test_distill_recovers_on_retry():
    calls = 0

    def handler(req):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"message": {"content": "not-json"}})
        return httpx.Response(200, json=_valid_chat_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result, _, _ = await distill(
            client, "http://x", "q", "Q?", "md", timeout_s=10
        )
    assert result.answer == "Yes."
    assert calls == 2


@pytest.mark.asyncio
async def test_distill_unavailable_on_connect_error():
    def handler(req):
        raise httpx.ConnectError("nope")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OllamaUnavailableError):
            await distill(client, "http://x", "q", "Q?", "md", timeout_s=10)


@pytest.mark.asyncio
async def test_distill_timeout():
    def handler(req):
        raise httpx.ReadTimeout("slow")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OllamaTimeoutError):
            await distill(client, "http://x", "q", "Q?", "md", timeout_s=10)


@pytest.mark.asyncio
async def test_distill_unavailable_on_4xx():
    def handler(req):
        return httpx.Response(503, text="down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OllamaUnavailableError):
            await distill(client, "http://x", "q", "Q?", "md", timeout_s=10)


def test_truncate_passthrough():
    md = "abc"
    out, trunc = truncate(md, limit=10)
    assert out == "abc"
    assert trunc is False


def test_truncate_cuts():
    out, trunc = truncate("a" * 100, limit=10)
    assert len(out) == 10
    assert trunc is True


def test_build_prompt_includes_question_and_markdown():
    p = build_prompt("How many?", "this is the page")
    assert "How many?" in p
    assert "this is the page" in p
    assert "verbatim" in p.lower()
