from __future__ import annotations

import sys
from urllib.parse import urlsplit

import httpx
from protego import Protego


class RobotsCache:
    """Per-run robots.txt cache, keyed by scheme+host.

    Lives for the duration of a single `decant url` invocation; nothing is
    persisted to disk. Missing or unreachable robots.txt is treated as
    allow-all, matching standard crawler behavior.

    When no client is injected, one is created and leaked at process exit
    (fine for a short-lived CLI). Callers that want explicit teardown
    should pass an `httpx.Client` they own.
    """

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=10, follow_redirects=True)
        self._cache: dict[str, Protego | None] = {}

    def is_allowed(self, url: str, user_agent: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rp = self._cache.get(origin, _SENTINEL)
        if rp is _SENTINEL:
            rp = self._fetch(origin, user_agent)
            self._cache[origin] = rp
        return True if rp is None else rp.can_fetch(url, user_agent)

    def _fetch(self, origin: str, user_agent: str) -> Protego | None:
        try:
            r = self._client.get(
                f"{origin}/robots.txt", headers={"User-Agent": user_agent}
            )
        except httpx.HTTPError as e:
            print(f"robots.txt unreachable for {origin}: {e}", file=sys.stderr)
            return None
        if r.status_code >= 400:
            return None
        return Protego.parse(r.text)


_SENTINEL = object()
