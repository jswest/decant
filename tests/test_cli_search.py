from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from click.testing import CliRunner

from decant import cache as cache_mod
from decant.cli import main
from decant.search import (
    SearchBadQueryError,
    SearchRateLimitedError,
    SearchUnauthorizedError,
    SearchUnavailableError,
)


@pytest.fixture
def patched_paths(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cfg_path.write_text(
        "contact_email: a@b.co\n"
        "search:\n"
        "  brave_api_key: brv-test\n"
        "  default_top: 10\n"
        "  default_token_budget: 4096\n"
        "  default_country: us\n"
        "  default_lang: en\n"
        "  ttl_hours: 1\n"
    )
    monkeypatch.setattr("decant.cli.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("decant.cli.CACHE_DIR", cache_dir)
    monkeypatch.setattr("decant.config.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("decant.config.CACHE_DIR", cache_dir)
    # Drop the env override so test config is what's exercised.
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)
    return cfg_path, cache_dir


@pytest.fixture
def stub_search(monkeypatch):
    """Make `decant.cli.search` a deterministic AsyncMock."""
    mock = AsyncMock(
        return_value=(
            {
                "results": [
                    {
                        "url": "https://a.com/x",
                        "title": "X",
                        "hostname": "a.com",
                        "age": None,
                        "snippets": ["s1"],
                    }
                ]
            },
            42,
        )
    )
    monkeypatch.setattr("decant.cli.search", mock)
    return mock


# ---------------------------------------------------------------------------


def test_happy_path_emits_documented_schema(patched_paths, stub_search):
    result = CliRunner().invoke(main, ["search", "qwen3 license"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["query"] == "qwen3 license"
    assert payload["fetched_at"].endswith("Z")
    assert len(payload["results"]) == 1
    assert payload["results"][0]["url"] == "https://a.com/x"
    assert payload["meta"]["brave_ms"] == 42
    assert payload["meta"]["cached"] is False
    assert payload["meta"]["token_budget"] == 4096
    assert payload["meta"]["result_count"] == 1


def test_overrides_passed_through_to_search(patched_paths, stub_search):
    result = CliRunner().invoke(
        main,
        [
            "search",
            "q",
            "--top",
            "5",
            "--token-budget",
            "8192",
            "--country",
            "uk",
            "--lang",
            "fr",
            "--freshness",
            "pw",
        ],
    )
    assert result.exit_code == 0, result.output
    _, kwargs = stub_search.call_args
    assert kwargs == {
        "top": 5,
        "token_budget": 8192,
        "country": "uk",
        "lang": "fr",
        "freshness": "pw",
    }


def test_missing_api_key_returns_search_no_api_key(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("contact_email: a@b.co\n")  # no search block
    monkeypatch.setattr("decant.cli.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("decant.config.CONFIG_PATH", cfg_path)
    monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

    result = CliRunner().invoke(main, ["search", "q"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["code"] == "search_no_api_key"


def test_config_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("decant.cli.CONFIG_PATH", tmp_path / "nope.yaml")
    monkeypatch.setattr("decant.config.CONFIG_PATH", tmp_path / "nope.yaml")

    result = CliRunner().invoke(main, ["search", "q"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["code"] == "config_missing"


@pytest.mark.parametrize(
    "exc,expected_code",
    [
        (SearchBadQueryError("bad"), "search_bad_query"),
        (SearchUnauthorizedError("401"), "search_unauthorized"),
        (SearchUnavailableError("net"), "search_unavailable"),
    ],
)
def test_exception_to_error_code_mapping(patched_paths, monkeypatch, exc, expected_code):
    monkeypatch.setattr("decant.cli.search", AsyncMock(side_effect=exc))

    result = CliRunner().invoke(main, ["search", "q"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["code"] == expected_code
    assert payload["query"] == "q"


def test_rate_limited_includes_retry_after(patched_paths, monkeypatch):
    monkeypatch.setattr(
        "decant.cli.search", AsyncMock(side_effect=SearchRateLimitedError(retry_after=5.0))
    )
    result = CliRunner().invoke(main, ["search", "q"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["code"] == "search_rate_limited"
    assert payload["details"]["retry_after"] == 5.0


def test_rate_limited_omits_details_when_no_retry_after(patched_paths, monkeypatch):
    monkeypatch.setattr(
        "decant.cli.search", AsyncMock(side_effect=SearchRateLimitedError(retry_after=None))
    )
    result = CliRunner().invoke(main, ["search", "q"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["code"] == "search_rate_limited"
    assert "details" not in payload


def test_successful_search_writes_to_cache(patched_paths, stub_search):
    _, cache_dir = patched_paths
    result = CliRunner().invoke(main, ["search", "fresh"])
    assert result.exit_code == 0, result.output
    key = cache_mod.search_cache_key("fresh", 10, None, "us", "en", 4096)
    assert (cache_dir / f"{key}.json").exists()


def test_cache_hit_short_circuits_search(patched_paths, monkeypatch):
    _, cache_dir = patched_paths
    key = cache_mod.search_cache_key("cached q", 10, None, "us", "en", 4096)
    cached = {
        "query": "cached q",
        "fetched_at": "2026-05-26T00:00:00Z",
        "results": [],
        "meta": {"cached": False, "brave_ms": 0, "token_budget": 4096, "result_count": 0},
    }
    cache_mod.write(key, cached, cache_dir=cache_dir)

    def explode(*a, **kw):
        raise AssertionError("search() should not be called on cache hit")

    monkeypatch.setattr("decant.cli.search", explode)

    result = CliRunner().invoke(main, ["search", "cached q"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["meta"]["cached"] is True
    assert payload["meta"]["cached_at"].endswith("Z")


def test_no_cache_skips_read_and_write(patched_paths, stub_search):
    _, cache_dir = patched_paths
    key = cache_mod.search_cache_key("fresh", 10, None, "us", "en", 4096)
    poisoned = {"query": "fresh", "results": [], "meta": {"cached": False}, "POISON": True}
    cache_mod.write(key, poisoned, cache_dir=cache_dir)

    result = CliRunner().invoke(main, ["search", "fresh", "--no-cache"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "POISON" not in payload  # didn't read cache
    # Didn't overwrite the poisoned entry either.
    on_disk = json.loads((cache_dir / f"{key}.json").read_text())
    assert on_disk.get("POISON") is True


def test_brave_api_key_env_override(tmp_path, monkeypatch, stub_search):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("contact_email: a@b.co\n")
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr("decant.cli.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("decant.cli.CACHE_DIR", cache_dir)
    monkeypatch.setattr("decant.config.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("decant.config.CACHE_DIR", cache_dir)
    monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brv-from-env")

    result = CliRunner().invoke(main, ["search", "q"])
    assert result.exit_code == 0, result.output
    args, _ = stub_search.call_args
    # search() positional args: (client, api_key, query, ...)
    assert args[1] == "brv-from-env"
