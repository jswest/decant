import json
from pathlib import Path

import pytest

from decant import extract as extract_mod
from decant.extract import ExtractEmptyError, extract

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


def _load_article(name: str) -> dict:
    return json.loads(_load(name))


@pytest.fixture
def force_bs4(monkeypatch):
    """Force the chain to fall all the way through to the bs4 tier."""
    monkeypatch.setattr(extract_mod, "_trafilatura", lambda html: None)


def test_trafilatura_handles_article():
    md, name = extract(_load("article.html"))
    assert name == "trafilatura"
    assert "introductory paragraph" in md
    assert "Home | About | Contact" not in md  # nav stripped


def test_trafilatura_preserves_links():
    md, _ = extract(_load("article.html"))
    assert "https://example.com/related" in md


def test_readability_js_wins_when_article_present():
    """A Fetcher-supplied article dict short-circuits the rest of the chain."""
    body = "<p>Body paragraph with enough content to clear the quality bar. " * 8 + "</p>"
    article = {"content": f"<h1>Headline</h1>{body}"}
    md, name = extract(_load("article.html"), article)
    assert name == "readability.js"
    assert "Headline" in md
    # We did not fall through to trafilatura's view of the fallback HTML.
    assert "introductory paragraph" not in md


@pytest.mark.parametrize("content", ["", "<p>tiny stub</p>"])
def test_low_quality_article_falls_through_to_trafilatura(content):
    md, name = extract(_load("article.html"), {"content": content})
    assert name == "trafilatura"


def test_falls_through_to_bs4_when_trafilatura_empty(force_bs4):
    md, name = extract(_load("article.html"))
    assert name == "bs4+html2text"
    assert "introductory paragraph" in md


def test_empty_html_raises():
    with pytest.raises(ExtractEmptyError):
        extract(_load("empty.html"))


def test_bs4_strips_script_and_style(force_bs4):
    html = """
    <html><body>
    <script>alert('xss')</script>
    <style>body { color: red }</style>
    <main><p>Real content.</p></main>
    </body></html>
    """
    md, name = extract(html)
    assert name == "bs4+html2text"
    assert "alert" not in md
    assert "color: red" not in md
    assert "Real content" in md


def test_bs4_prefers_main_over_article_and_role(force_bs4):
    html = """
    <html><body>
    <article>From article.</article>
    <div role="main">From role=main.</div>
    <main>From main.</main>
    </body></html>
    """
    md, _ = extract(html)
    assert "From main." in md
    assert "From article." not in md
    assert "From role=main." not in md


def test_bs4_prefers_article_over_role_main(force_bs4):
    html = """
    <html><body>
    <div role="main">From role=main.</div>
    <article>From article.</article>
    </body></html>
    """
    md, _ = extract(html)
    assert "From article." in md
    assert "From role=main." not in md


def test_bs4_falls_back_to_body(force_bs4):
    html = "<html><body><p>Only body content.</p></body></html>"
    md, _ = extract(html)
    assert "Only body content." in md


def test_api_reference_extracted_via_readability_js():
    """Regression test for issue #6 — Brave Web Search API reference.

    Fixture captured against
    https://api-dashboard.search.brave.com/api-reference/web/search/get
    by running the Fetcher (which now sets bypass_csp=True) and dumping
    its FetchResult.article to JSON.

    Readability.js captures the main article body, including the `q`
    parameter (description "search query term") and the auth header's
    description ("subscription token"). The literal "X-Subscription-Token"
    string is unreachable in any extractor on this page — it only lives
    in shiki-syntax-highlighted code blocks (token-split across spans)
    and in `data-copy-text` attributes, neither of which is text content
    that an extractor walks.
    """
    article = _load_article("api_reference.article.json")
    md, name = extract("", article)
    assert name == "readability.js"
    assert "search query term" in md
    assert "subscription token" in md.lower()
