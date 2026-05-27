import json

import pytest
from click.testing import CliRunner

from decant import __version__
from decant.cli import main


@pytest.fixture
def patched_paths(tmp_path, monkeypatch):
    """Redirect config and cache paths into tmp_path for the duration of a test."""
    cfg_path = tmp_path / "config.yaml"
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr("decant.cli.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("decant.cli.CACHE_DIR", cache_dir)
    monkeypatch.setattr("decant.config.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("decant.config.CACHE_DIR", cache_dir)
    return cfg_path, cache_dir


def test_version_prints_json():
    runner = CliRunner()
    result = runner.invoke(main, ["version"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["version"] == __version__
    assert payload["config_path"].endswith("config.yaml")
    assert payload["cache_dir"].endswith("cache")


def test_config_interactive_writes_file(patched_paths, monkeypatch):
    cfg_path, cache_dir = patched_paths
    monkeypatch.setattr(
        "decant.cli._list_ollama_models", lambda host: ["qwen3:32b", "qwen3:8b"]
    )

    # Prompts: email, ollama host, model, fast model (skip), cache TTL, brave key.
    result = CliRunner().invoke(
        main,
        ["config"],
        input="alice@example.com\nhttp://localhost:11434\nqwen3:32b\n\n24\n\n",
    )
    assert result.exit_code == 0, result.output
    text = cfg_path.read_text()
    assert "alice@example.com" in text
    assert "fast_model: null" in text
    assert cache_dir.is_dir()


def test_config_re_prompts_on_bad_email(patched_paths, monkeypatch):
    monkeypatch.setattr("decant.cli._list_ollama_models", lambda host: [])

    result = CliRunner().invoke(
        main,
        ["config"],
        input="not-an-email\nalice@example.com\nhttp://localhost:11434\nqwen3:32b\n\n24\n\n",
    )
    assert result.exit_code == 0, result.output
    assert "Not a plausible email" in result.output


def test_config_persists_brave_api_key(patched_paths, monkeypatch):
    cfg_path, _ = patched_paths
    monkeypatch.setattr("decant.cli._list_ollama_models", lambda host: ["qwen3:32b"])

    result = CliRunner().invoke(
        main,
        ["config"],
        input="alice@example.com\nhttp://localhost:11434\nqwen3:32b\n\n24\nbrv-abc123\n",
    )
    assert result.exit_code == 0, result.output
    assert "brv-abc123" in cfg_path.read_text()


def test_config_persists_fast_model_when_provided(patched_paths, monkeypatch):
    cfg_path, _ = patched_paths
    monkeypatch.setattr(
        "decant.cli._list_ollama_models", lambda host: ["qwen3:32b", "qwen3:8b"]
    )

    result = CliRunner().invoke(
        main,
        ["config"],
        input="alice@example.com\nhttp://localhost:11434\nqwen3:32b\nqwen3:8b\n24\n\n",
    )
    assert result.exit_code == 0, result.output
    assert "fast_model: qwen3:8b" in cfg_path.read_text()


def test_config_announces_fast_default_when_fast_model_set(
    patched_paths, monkeypatch
):
    """Issue #23: callers configuring fast_model should learn that summary mode
    now defaults to the fast tier, with --accurate as the opt-in for the
    previous behavior."""
    monkeypatch.setattr(
        "decant.cli._list_ollama_models", lambda host: ["qwen3:32b", "qwen3:8b"]
    )

    result = CliRunner().invoke(
        main,
        ["config"],
        input="alice@example.com\nhttp://localhost:11434\nqwen3:32b\nqwen3:8b\n24\n\n",
    )
    assert result.exit_code == 0, result.output
    assert "default to the fast tier" in result.output
    assert "--accurate" in result.output


def test_config_no_fast_default_note_when_fast_model_unset(
    patched_paths, monkeypatch
):
    monkeypatch.setattr("decant.cli._list_ollama_models", lambda host: ["qwen3:32b"])

    result = CliRunner().invoke(
        main,
        ["config"],
        input="alice@example.com\nhttp://localhost:11434\nqwen3:32b\n\n24\n\n",
    )
    assert result.exit_code == 0, result.output
    assert "default to the fast tier" not in result.output
