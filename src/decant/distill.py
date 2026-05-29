from __future__ import annotations

import time

import httpx
from pydantic import ValidationError

from decant.models import DistillResult, TerseDistillResult

MAX_MARKDOWN_CHARS = 80_000

_PROMPT_TEMPLATE = """\
You are extracting information from a web page to help answer a research question.

QUESTION:
{question}

PAGE CONTENT (markdown):
---
{markdown}
---

Return ONLY a JSON object with this exact schema:

{{
  "answer": "<direct answer to the question in 1-3 sentences, or null if the page does not answer it>",
  "page_topic": "<one sentence describing what this page is about>",
  "findings": [
    {{
      "quote": "<verbatim text from the page, up to 200 words per quote>",
      "context": "<the section heading or surrounding context>",
      "relevance": "high" | "medium" | "low"
    }}
  ]
}}

RULES:
- Quotes must be verbatim. Do not paraphrase, do not edit for length other than truncating with an ellipsis if needed.
- Maximum 8 findings.
- If the page does not answer the question, set "answer" to null. Still include up to 3 tangentially relevant findings if any exist.
- Do not invent content not present in the page.
- Output ONLY the JSON object. No commentary, no markdown fences.
"""

_TERSE_PROMPT_TEMPLATE = """\
You are extracting information from a web page to help answer a research question.

QUESTION:
{question}

PAGE CONTENT (markdown):
---
{markdown}
---

Return ONLY a JSON object with this exact schema:

{{
  "answer": "<direct answer to the question in 1-3 sentences, or null if the page does not answer it>",
  "page_topic": "<one sentence describing what this page is about>",
  "findings": [
    {{
      "context": "<the section heading or surrounding context where the supporting text lives>",
      "relevance": "high" | "medium" | "low"
    }}
  ]
}}

RULES:
- Answer concisely. Do not include source passages — the caller has opted out of verbatim quotes.
- Each finding points at WHERE on the page the answer is grounded (section/heading), not WHAT it says.
- Maximum 8 findings.
- If the page does not answer the question, set "answer" to null. Still include up to 3 tangentially relevant findings if any exist.
- Do not invent content not present in the page.
- Output ONLY the JSON object. No commentary, no markdown fences.
"""


class OllamaUnavailableError(Exception):
    """Could not reach the configured Ollama host."""


class OllamaTimeoutError(Exception):
    """`/api/chat` exceeded the configured timeout."""


class OllamaBadJsonError(Exception):
    """Model returned non-JSON or schema-incompatible JSON after one retry."""


def truncate(markdown: str, limit: int = MAX_MARKDOWN_CHARS) -> tuple[str, bool]:
    if len(markdown) <= limit:
        return markdown, False
    return markdown[:limit], True


def build_prompt(question: str, markdown: str, terse: bool = False) -> str:
    template = _TERSE_PROMPT_TEMPLATE if terse else _PROMPT_TEMPLATE
    return template.format(question=question, markdown=markdown)


async def distill(
    client: httpx.AsyncClient,
    host: str,
    model: str,
    question: str,
    markdown: str,
    keep_alive: str = "5m",
    timeout_s: int,
    terse: bool = False,
) -> tuple[DistillResult | TerseDistillResult, int, bool]:
    """POST to Ollama's /api/chat, validate the response against the right schema.

    Returns (result, elapsed_ms, truncated). When terse=True, the response is
    constrained to TerseDistillResult (no verbatim quotes). Retries once on
    validation failure with the same payload, since (per spec) schema-
    constrained generation should make malformed JSON very rare.
    """
    md, truncated = truncate(markdown)
    result_cls = TerseDistillResult if terse else DistillResult
    body = {
        "model": model,
        "messages": [{"role": "user", "content": build_prompt(question, md, terse)}],
        "format": result_cls.model_json_schema(),
        "stream": False,
        "keep_alive": keep_alive,
    }
    url = f"{host.rstrip('/')}/api/chat"

    start = time.monotonic()
    last_err: ValidationError | None = None
    for _ in range(2):
        try:
            r = await client.post(url, json=body, timeout=timeout_s)
        except httpx.TimeoutException as e:
            raise OllamaTimeoutError(str(e)) from e
        except httpx.HTTPError as e:
            raise OllamaUnavailableError(str(e)) from e

        if r.status_code >= 400:
            raise OllamaUnavailableError(f"HTTP {r.status_code}: {r.text[:200]}")

        content = r.json().get("message", {}).get("content", "")
        try:
            result = result_cls.model_validate_json(content)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return result, elapsed_ms, truncated
        except ValidationError as e:
            last_err = e
            continue

    raise OllamaBadJsonError(str(last_err))
