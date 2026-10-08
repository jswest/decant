from __future__ import annotations

import json
import sys

import pytest
from click.testing import CliRunner

from decant.cli import main


@pytest.fixture
def patched_paths(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setattr("decant.cli.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("decant.cli.CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr("decant.config.CONFIG_PATH", cfg_path)
    monkeypatch.setattr("decant.config.CACHE_DIR", tmp_path / "cache")
    return cfg_path


@pytest.fixture
def calls(monkeypatch):
    """Record subprocess calls and pretend Chromium launches fine."""
    recorded: list[list[str]] = []
    monkeypatch.setattr(
        "decant.cli.subprocess.run", lambda cmd, **kw: recorded.append(cmd)
    )
    monkeypatch.setattr("decant.cli._check_browser", lambda: None)
    return recorded


def test_setup_installs_chromium_with_own_interpreter(patched_paths, calls, monkeypatch):
    monkeypatch.setattr("decant.cli._list_ollama_models", lambda host: [])
    result = CliRunner().invoke(main, ["setup"])
    assert result.exit_code == 0, result.output
    assert calls == [[sys.executable, "-m", "playwright", "install", "chromium"]]


def test_setup_reports_missing_config_and_no_ollama(patched_paths, calls, monkeypatch):
    monkeypatch.setattr("decant.cli._list_ollama_models", lambda host: [])
    payload = json.loads(CliRunner().invoke(main, ["setup"]).output)
    assert payload["browser"] == {"ok": True}
    assert payload["config"] == "missing"
    assert payload["contact_email"] is None
    assert payload["ollama"]["reachable"] is False
    assert payload["brave_key"] is False


def test_setup_reports_config_and_ollama_models(patched_paths, calls, monkeypatch):
    patched_paths.write_text(
        "contact_email: a@b.co\nsearch:\n  brave_api_key: brv-x\n"
    )
    monkeypatch.setattr(
        "decant.cli._list_ollama_models", lambda host: ["qwen3:8b", "qwen3:32b"]
    )
    payload = json.loads(CliRunner().invoke(main, ["setup"]).output)
    assert payload["config"] == "ok"
    assert payload["contact_email"] == "a@b.co"
    assert payload["ollama"]["reachable"] is True
    assert payload["ollama"]["models"] == ["qwen3:8b", "qwen3:32b"]
    assert payload["brave_key"] is True


def test_setup_browser_failure_exits_nonzero(patched_paths, calls, monkeypatch):
    monkeypatch.setattr("decant.cli._list_ollama_models", lambda host: [])
    monkeypatch.setattr("decant.cli._check_browser", lambda: "missing libnss3")
    monkeypatch.setattr("decant.cli.platform.system", lambda: "Linux")
    result = CliRunner().invoke(main, ["setup"])
    assert result.exit_code == 1
    browser = json.loads(result.output)["browser"]
    assert browser["ok"] is False
    assert "install-deps" in browser["fix"]
