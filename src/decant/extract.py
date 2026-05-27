"""Three-tier markdown extraction.

Tier 1 — Mozilla Readability.js (consumed via the article dict the Fetcher
captures in-browser). Tier 2 — trafilatura. Tier 3 — bs4+html2text (safety
net). Tiers 1 and 2 gate on a 250-char length floor; tier 3 only needs
non-empty output.
"""

from __future__ import annotations

import html2text
import trafilatura
from bs4 import BeautifulSoup
from markdownify import markdownify


_MIN_QUALITY_CHARS = 250


class ExtractEmptyError(Exception):
    """All extractors returned empty/whitespace."""


def extract(html: str, article: dict | None = None) -> tuple[str, str]:
    """Run the extractor chain; return (markdown, extractor_name)."""
    if article is not None:
        md = markdownify(article.get("content") or "")
        if _is_quality(md):
            return (md, "readability.js")
    md = _trafilatura(html)
    if _is_quality(md):
        return (md, "trafilatura")
    md = _bs4(html)
    if md and md.strip():
        return (md, "bs4+html2text")
    raise ExtractEmptyError("all extractors returned empty")


def _is_quality(content: str | None) -> bool:
    return bool(content) and len(content) >= _MIN_QUALITY_CHARS


def _trafilatura(html: str) -> str | None:
    return trafilatura.extract(html, output_format="markdown", include_links=True)


def _bs4(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "aside"]):
        tag.decompose()
    container = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.body
        or soup
    )
    h = html2text.HTML2Text()
    h.ignore_images = True
    h.body_width = 0
    return h.handle(str(container))
