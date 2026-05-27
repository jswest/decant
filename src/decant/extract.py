from __future__ import annotations

import html2text
import trafilatura
from bs4 import BeautifulSoup
from markdownify import markdownify
from readability import Document


class ExtractEmptyError(Exception):
    """All three extractors returned empty/whitespace."""


def extract(html: str) -> tuple[str, str]:
    """Run the three-tier extractor chain; return (markdown, extractor_name)."""
    for name, fn in (
        ("trafilatura", _trafilatura),
        ("readability", _readability),
        ("bs4+html2text", _bs4),
    ):
        md = fn(html)
        if md and md.strip():
            return (md, name)
    raise ExtractEmptyError("all extractors returned empty")


def _trafilatura(html: str) -> str | None:
    return trafilatura.extract(html, output_format="markdown", include_links=True)


def _readability(html: str) -> str:
    summary_html = Document(html).summary()
    return markdownify(summary_html)


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
