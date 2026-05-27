from pathlib import Path

import pytest

from decant import extract as extract_mod
from decant.extract import ExtractEmptyError, extract

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.fixture
def force_bs4(monkeypatch):
    """Force the chain to fall all the way through to the bs4 tier."""
    monkeypatch.setattr(extract_mod, "_trafilatura", lambda html: None)
    monkeypatch.setattr(extract_mod, "_readability", lambda html: "")


def test_trafilatura_handles_article():
    md, name = extract(_load("article.html"))
    assert name == "trafilatura"
    assert "introductory paragraph" in md
    assert "Home | About | Contact" not in md  # nav stripped


def test_trafilatura_preserves_links():
    md, _ = extract(_load("article.html"))
    assert "https://example.com/related" in md


def test_falls_through_to_readability_when_trafilatura_empty(monkeypatch):
    monkeypatch.setattr(extract_mod, "_trafilatura", lambda html: None)
    md, name = extract(_load("article.html"))
    assert name == "readability"
    assert md.strip()


def test_falls_through_to_bs4_when_first_two_empty(force_bs4):
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
