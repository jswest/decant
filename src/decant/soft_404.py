"""Soft-404 detector.

A "soft 404" is a page that returns HTTP 200 but doesn't represent the
requested resource — typically an SPA route handler rendering the
landing page (or some error stub) without setting a 4xx status.

This module is pure: no I/O, no network. It runs on data already in
hand after fetch + extract — the requested URL, the URL the page
actually settled on (post-redirect), the raw HTML, the extracted
markdown, and the page title — and returns a `{verdict, reasons}` dict
that the caller surfaces in `meta.soft_404`. We do not auto-error;
the caller decides what to do with the verdict.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from bs4 import BeautifulSoup

MIN_EXTRACT_CHARS = 200

_TITLE_404_PHRASES = ("404", "not found", "doesn't exist")

_STRONG_SIGNALS = frozenset({"title_contains_404", "canonical_mismatch"})


def detect(
    final_url: str,
    html: str,
    markdown: str,
    title: str,
) -> dict:
    """Return ``{"verdict": ..., "reasons": [...]}`` for the given fetch.

    Verdict is ``"likely"`` if any strong signal trips, ``"possible"`` if
    only soft signals trip, ``"unlikely"`` otherwise. Reasons are listed
    in stable order: title → canonical → short.
    """
    reasons: list[str] = []

    if _title_looks_like_404(title):
        reasons.append("title_contains_404")
    if _canonical_disagrees(html, final_url):
        reasons.append("canonical_mismatch")
    if len(markdown.strip()) < MIN_EXTRACT_CHARS:
        reasons.append("extract_too_short")

    if any(r in _STRONG_SIGNALS for r in reasons):
        verdict = "likely"
    elif reasons:
        verdict = "possible"
    else:
        verdict = "unlikely"

    return {"verdict": verdict, "reasons": reasons}


def _title_looks_like_404(title: str) -> bool:
    lowered = title.lower()
    return any(phrase in lowered for phrase in _TITLE_404_PHRASES)


def _canonical_disagrees(html: str, final_url: str) -> bool:
    """True if the page declares a canonical URL whose host+path differs from final_url.

    Both `<link rel=canonical>` and `<meta property=og:url>` are checked; either
    one disagreeing with `final_url` trips the signal, even if the other agrees.
    Scheme is intentionally ignored — a https final_url vs http canonical is not
    a soft-404 signal.
    """
    target = _normalize(final_url)
    if target is None:
        return False

    soup = BeautifulSoup(html, "html.parser")
    declared = []
    link = soup.find("link", rel="canonical")
    if link and link.get("href"):
        declared.append(link["href"])
    og = soup.find("meta", attrs={"property": "og:url"})
    if og and og.get("content"):
        declared.append(og["content"])

    for d in declared:
        norm = _normalize(d)
        if norm is not None and norm != target:
            return True
    return False


def _normalize(url: str) -> tuple[str, str] | None:
    """Return (host, path) with trailing slash stripped (root preserved). None if unparseable."""
    parts = urlsplit(url)
    if not parts.netloc:
        return None
    path = (parts.path or "/").rstrip("/") or "/"
    return (parts.netloc.lower(), path)
