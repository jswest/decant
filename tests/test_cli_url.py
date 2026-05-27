from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from decant.cli import main
from decant.fetch import FetchResult
from decant.models import DistillResult


@pytest.fixture
def patched_paths(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cfg_path.write_text(
        "contact_email: a@b.co\n"
        "ollama:\n  host: http://ollama.local\n  model: qwen3:32b\n"
    )
    monkeypatch.setattr("decant.cli.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("decant.cli.CACHE_DIR", cache_dir)
    monkeypatch.setattr("decant.config.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("decant.config.CACHE_DIR", cache_dir)
    return cfg_path, cache_dir


@pytest.fixture
def stub_pipeline(monkeypatch):
    """Replace fetch/extract/distill with deterministic stubs."""

    class StubFetcher:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def fetch(self, url, **_):
            return FetchResult(
                final_url=url, title="T", html="<p>x</p>", elapsed_ms=10
            )

    class StubRobots:
        def is_allowed(self, url, ua):
            return True

    monkeypatch.setattr("decant.cli.Fetcher", StubFetcher)
    monkeypatch.setattr("decant.cli.RobotsCache", lambda: StubRobots())
    monkeypatch.setattr(
        "decant.cli.extract_html", lambda html: ("# Title\n\nbody", "trafilatura")
    )
    distill_mock = AsyncMock(
        return_value=(
            DistillResult(answer="Y.", page_topic="topic", findings=[]),
            42,
            False,
        )
    )
    monkeypatch.setattr("decant.cli.distill", distill_mock)
    return distill_mock


# ---------------------------------------------------------------------------


def test_invalid_url_returns_error_object(patched_paths, stub_pipeline):
    result = CliRunner().invoke(main, ["url", "ftp://x.com"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["code"] == "invalid_url"


def test_extract_mode_default_no_question(patched_paths, stub_pipeline):
    result = CliRunner().invoke(main, ["url", "https://example.com"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "extract"
    assert "markdown" in payload["extract"]
    assert "summary" not in payload
    assert "ollama_ms" not in payload["meta"]


def test_summary_mode_default_with_question(patched_paths, stub_pipeline):
    result = CliRunner().invoke(
        main, ["url", "https://example.com", "--question", "what?"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "summary"
    assert "markdown" not in payload["extract"]
    assert payload["summary"]["answer"] == "Y."
    assert payload["meta"]["ollama_ms"] == 42


def test_both_mode_includes_markdown_and_summary(patched_paths, stub_pipeline):
    result = CliRunner().invoke(
        main,
        ["url", "https://example.com", "--question", "what?", "--mode", "both"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["mode"] == "both"
    assert "markdown" in payload["extract"]
    assert "summary" in payload


def test_summary_without_question_rejected(patched_paths, stub_pipeline):
    result = CliRunner().invoke(
        main, ["url", "https://example.com", "--mode", "summary"]
    )
    assert result.exit_code != 0
    assert "requires --question" in result.output


def test_multiple_urls_emit_array_in_order(patched_paths, stub_pipeline):
    result = CliRunner().invoke(
        main, ["url", "https://a.com", "https://b.com"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert isinstance(payload, list)
    assert [p["url"] for p in payload] == ["https://a.com", "https://b.com"]


def test_exit_nonzero_when_any_url_errors(patched_paths, stub_pipeline):
    result = CliRunner().invoke(
        main, ["url", "https://ok.com", "ftp://bad.com"]
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert "error" not in payload[0]
    assert payload[1]["code"] == "invalid_url"


def test_config_missing_emits_code(tmp_path, monkeypatch):
    monkeypatch.setattr("decant.cli.CONFIG_PATH", tmp_path / "nope.yaml")
    monkeypatch.setattr("decant.config.CONFIG_PATH", tmp_path / "nope.yaml")
    result = CliRunner().invoke(main, ["url", "https://example.com"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["code"] == "config_missing"


def test_cache_hit_short_circuits_pipeline(patched_paths, stub_pipeline, monkeypatch):
    from decant import cache as cache_mod

    _, cache_dir = patched_paths
    url = "https://cached.example.com"
    mode = "extract"
    key = cache_mod.cache_key(url, mode, None, "qwen3:32b")

    cached = {
        "url": url,
        "final_url": url,
        "title": "Cached!",
        "fetched_at": "2026-05-26T00:00:00Z",
        "mode": "extract",
        "extract": {"extractor": "trafilatura", "char_count": 9, "markdown": "x"},
        "meta": {"cached": False, "model": "qwen3:32b"},
    }
    cache_mod.write(key, cached, cache_dir=cache_dir)

    # Confirm cache short-circuit: any fetch.fetch() call should abort the test.
    class ExplodingFetcher:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def fetch(self, *a, **kw):
            raise AssertionError("fetch should not be called on cache hit")

    def explode_extract(html):
        raise AssertionError("extract should not be called on cache hit")

    monkeypatch.setattr("decant.cli.Fetcher", ExplodingFetcher)
    monkeypatch.setattr("decant.cli.extract_html", explode_extract)

    result = CliRunner().invoke(main, ["url", url])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["title"] == "Cached!"
    assert payload["meta"]["cached"] is True
    assert payload["meta"]["cached_at"].endswith("Z")
