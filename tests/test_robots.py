from __future__ import annotations

import httpx

from decant.robots import RobotsCache


def _cache(handler) -> RobotsCache:
    return RobotsCache(httpx.Client(transport=httpx.MockTransport(handler)))


UA = "Decant/0.1 (+mailto:test@example.com)"


def test_missing_robots_txt_allows():
    rc = _cache(lambda req: httpx.Response(404))
    assert rc.is_allowed("https://example.com/foo", UA) is True


def test_allow_all():
    rc = _cache(lambda req: httpx.Response(200, text="User-agent: *\nAllow: /\n"))
    assert rc.is_allowed("https://example.com/foo", UA) is True


def test_disallow_all():
    rc = _cache(lambda req: httpx.Response(200, text="User-agent: *\nDisallow: /\n"))
    assert rc.is_allowed("https://example.com/foo", UA) is False


def test_ua_specific_disallow():
    body = "User-agent: Decant\nDisallow: /private\n"
    rc = _cache(lambda req: httpx.Response(200, text=body))
    assert rc.is_allowed("https://example.com/public", UA) is True
    assert rc.is_allowed("https://example.com/private", UA) is False


def test_network_error_allows():
    def handler(req):
        raise httpx.ConnectError("boom")

    rc = _cache(handler)
    assert rc.is_allowed("https://example.com/foo", UA) is True


def test_5xx_treated_as_allow():
    rc = _cache(lambda req: httpx.Response(500, text="oops"))
    assert rc.is_allowed("https://example.com/foo", UA) is True


def test_per_host_caching_avoids_refetch():
    calls: list[str] = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")

    rc = _cache(handler)
    rc.is_allowed("https://example.com/a", UA)
    rc.is_allowed("https://example.com/b", UA)
    rc.is_allowed("https://other.com/a", UA)
    assert calls == [
        "https://example.com/robots.txt",
        "https://other.com/robots.txt",
    ]
