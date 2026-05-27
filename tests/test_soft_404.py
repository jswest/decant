import pytest

from decant.soft_404 import MIN_EXTRACT_CHARS, detect

LONG_MD = "x" * (MIN_EXTRACT_CHARS + 50)


def _make(html="", title="", markdown=LONG_MD, final_url="https://example.com/a"):
    return detect(final_url=final_url, html=html, markdown=markdown, title=title)


# --- Verdict combiner --------------------------------------------------------


def test_no_signals_is_unlikely():
    r = _make(html="<html></html>", title="A normal page")
    assert r == {"verdict": "unlikely", "reasons": []}


def test_only_short_extract_is_possible():
    r = _make(markdown="too short")
    assert r["verdict"] == "possible"
    assert r["reasons"] == ["extract_too_short"]


def test_title_404_is_likely():
    r = _make(title="404 — page not found")
    assert r["verdict"] == "likely"
    assert "title_contains_404" in r["reasons"]


def test_canonical_mismatch_is_likely():
    html = '<html><head><link rel="canonical" href="https://example.com/other"></head></html>'
    r = _make(html=html)
    assert r["verdict"] == "likely"
    assert "canonical_mismatch" in r["reasons"]


def test_reasons_are_stable_order():
    """title → canonical → short."""
    html = (
        '<html><head><link rel="canonical" href="https://example.com/other"></head>'
        "<body></body></html>"
    )
    r = _make(html=html, title="404", markdown="short")
    assert r["reasons"] == [
        "title_contains_404",
        "canonical_mismatch",
        "extract_too_short",
    ]
    assert r["verdict"] == "likely"


# --- title_contains_404 ------------------------------------------------------


@pytest.mark.parametrize(
    "title", ["404", "Not Found", "Page Not Found", "this page doesn't exist"]
)
def test_title_phrases_caught(title):
    assert "title_contains_404" in _make(title=title)["reasons"]


def test_title_case_insensitive():
    assert "title_contains_404" in _make(title="NOT FOUND")["reasons"]


def test_title_innocent_passes():
    assert "title_contains_404" not in _make(title="Annual report on found objects")["reasons"]
    # ^ Substring "found" alone doesn't trip — phrase must be "not found".


# --- canonical_mismatch ------------------------------------------------------


def test_canonical_match_does_not_trip():
    html = '<html><head><link rel="canonical" href="https://example.com/a"></head></html>'
    assert "canonical_mismatch" not in _make(html=html)["reasons"]


def test_og_url_mismatch_trips():
    html = '<html><head><meta property="og:url" content="https://example.com/other"></head></html>'
    assert "canonical_mismatch" in _make(html=html)["reasons"]


def test_canonical_trailing_slash_ignored():
    html = '<html><head><link rel="canonical" href="https://example.com/a/"></head></html>'
    assert "canonical_mismatch" not in _make(html=html, final_url="https://example.com/a")["reasons"]


def test_canonical_query_string_ignored():
    html = '<html><head><link rel="canonical" href="https://example.com/a?utm=x"></head></html>'
    assert "canonical_mismatch" not in _make(html=html)["reasons"]


def test_canonical_fragment_ignored():
    html = '<html><head><link rel="canonical" href="https://example.com/a#section"></head></html>'
    assert "canonical_mismatch" not in _make(html=html)["reasons"]


def test_canonical_host_case_insensitive():
    html = '<html><head><link rel="canonical" href="https://EXAMPLE.com/a"></head></html>'
    assert "canonical_mismatch" not in _make(html=html)["reasons"]


def test_canonical_path_difference_trips():
    html = '<html><head><link rel="canonical" href="https://example.com/different"></head></html>'
    assert "canonical_mismatch" in _make(html=html)["reasons"]


def test_canonical_host_difference_trips():
    html = '<html><head><link rel="canonical" href="https://other.com/a"></head></html>'
    assert "canonical_mismatch" in _make(html=html)["reasons"]


def test_no_canonical_tag_does_not_trip():
    assert "canonical_mismatch" not in _make(html="<html><head></head></html>")["reasons"]


def test_malformed_canonical_does_not_crash():
    html = '<html><head><link rel="canonical" href="not a url"></head></html>'
    # Should not raise; "not a url" lacks netloc → normalize returns None → no mismatch.
    assert "canonical_mismatch" not in _make(html=html)["reasons"]


def test_unparseable_final_url_short_circuits():
    """If final_url itself can't be normalized, no canonical signal fires."""
    html = '<html><head><link rel="canonical" href="https://example.com/a"></head></html>'
    r = _make(html=html, final_url="not even close")
    assert "canonical_mismatch" not in r["reasons"]


def test_canonical_scheme_difference_does_not_trip():
    """Different scheme but same host+path is not a soft-404 signal."""
    html = '<html><head><link rel="canonical" href="http://example.com/a"></head></html>'
    r = _make(html=html, final_url="https://example.com/a")
    assert "canonical_mismatch" not in r["reasons"]


# --- extract_too_short -------------------------------------------------------


def test_short_extract_trips():
    assert "extract_too_short" in _make(markdown="a" * (MIN_EXTRACT_CHARS - 1))["reasons"]


def test_at_threshold_does_not_trip():
    assert "extract_too_short" not in _make(markdown="a" * MIN_EXTRACT_CHARS)["reasons"]


def test_whitespace_only_extract_trips():
    assert "extract_too_short" in _make(markdown="   \n\t  ")["reasons"]
