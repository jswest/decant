import json
import os
import time

from decant import cache


def test_key_stable_for_same_inputs():
    a = cache.cache_key("https://x.com", "extract", None, "qwen3:32b")
    b = cache.cache_key("https://x.com", "extract", None, "qwen3:32b")
    assert a == b


def test_key_differs_per_question_and_model():
    base = cache.cache_key("https://x.com", "summary", "q1", "qwen3:32b")
    assert base != cache.cache_key("https://x.com", "summary", "q2", "qwen3:32b")
    assert base != cache.cache_key("https://x.com", "summary", "q1", "qwen3:70b")


def test_write_then_read_flips_cached_meta(tmp_path):
    key = "abc"
    payload = {"url": "https://x.com", "meta": {"cached": False}}
    cache.write(key, payload, cache_dir=tmp_path)
    got = cache.read(key, ttl_hours=1, cache_dir=tmp_path)
    assert got["meta"]["cached"] is True
    assert got["meta"]["cached_at"].endswith("Z")


def test_read_returns_none_after_ttl(tmp_path):
    key = "abc"
    cache.write(key, {"url": "x"}, cache_dir=tmp_path)
    # Backdate file by 2 hours.
    path = tmp_path / f"{key}.json"
    old = time.time() - 7200
    os.utime(path, (old, old))
    assert cache.read(key, ttl_hours=1, cache_dir=tmp_path) is None


def test_read_missing_returns_none(tmp_path):
    assert cache.read("nope", ttl_hours=1, cache_dir=tmp_path) is None


def test_clear_removes_entries_returns_counts(tmp_path):
    for i in range(3):
        (tmp_path / f"e{i}.json").write_text(json.dumps({"i": i}))
    count, freed = cache.clear(tmp_path)
    assert count == 3
    assert freed > 0
    assert list(tmp_path.iterdir()) == []
    assert tmp_path.exists()  # directory itself is preserved


def test_clear_on_missing_dir_is_zero(tmp_path):
    assert cache.clear(tmp_path / "missing") == (0, 0)


def test_stats_empty(tmp_path):
    assert cache.stats(tmp_path) == {
        "entries": 0,
        "total_bytes": 0,
        "oldest": None,
        "newest": None,
    }


def test_stats_with_entries(tmp_path):
    (tmp_path / "a.json").write_text("a")
    (tmp_path / "b.json").write_text("bb")
    s = cache.stats(tmp_path)
    assert s["entries"] == 2
    assert s["total_bytes"] == 3
    assert s["oldest"].endswith("Z")
    assert s["newest"].endswith("Z")
