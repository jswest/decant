from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from decant.config import CACHE_DIR


def cache_key(url: str, mode: str, question: str | None, model: str) -> str:
    """Compute a cache key.

    Extract-mode keys ignore `question` and `model` — the extracted markdown
    depends on neither. Summary/both keys include both, since the LLM output
    is question- and model-dependent.
    """
    if mode == "extract":
        raw = f"{url}|extract"
    else:
        raw = f"{url}|{mode}|{question or ''}|{model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def search_cache_key(
    query: str,
    top: int,
    freshness: str | None,
    country: str,
    lang: str,
    token_budget: int,
) -> str:
    raw = f"search|{query}|{top}|{freshness or ''}|{country}|{lang}|{token_budget}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entries(cache_dir: Path) -> list[Path]:
    if not cache_dir.exists():
        return []
    return [e for e in cache_dir.iterdir() if e.is_file()]


def _path_for(key: str, cache_dir: Path | None = None) -> Path:
    return (cache_dir or CACHE_DIR) / f"{key}.json"


def read(
    key: str, ttl_hours: int, cache_dir: Path | None = None
) -> dict | None:
    path = _path_for(key, cache_dir)
    if not path.exists():
        return None
    st = path.stat()
    if time.time() - st.st_mtime > ttl_hours * 3600:
        return None
    with path.open() as f:
        payload = json.load(f)
    payload.setdefault("meta", {})
    payload["meta"]["cached"] = True
    payload["meta"]["cached_at"] = _iso(st.st_mtime)
    return payload


def write(key: str, payload: dict, cache_dir: Path | None = None) -> None:
    path = _path_for(key, cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(payload, f)


def clear(cache_dir: Path | None = None) -> tuple[int, int]:
    count = 0
    freed = 0
    for entry in _entries(cache_dir or CACHE_DIR):
        freed += entry.stat().st_size
        entry.unlink()
        count += 1
    return (count, freed)


def stats(cache_dir: Path | None = None) -> dict:
    files = _entries(cache_dir or CACHE_DIR)
    if not files:
        return {"entries": 0, "total_bytes": 0, "oldest": None, "newest": None}
    stats_list = [f.stat() for f in files]
    mtimes = [s.st_mtime for s in stats_list]
    return {
        "entries": len(files),
        "total_bytes": sum(s.st_size for s in stats_list),
        "oldest": _iso(min(mtimes)),
        "newest": _iso(max(mtimes)),
    }
