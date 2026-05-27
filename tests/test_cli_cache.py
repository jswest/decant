import json

from click.testing import CliRunner

from decant.cli import main


def test_cache_clear(tmp_path, monkeypatch):
    monkeypatch.setattr("decant.cli.CACHE_DIR", tmp_path)
    (tmp_path / "e.json").write_text("{}")

    result = CliRunner().invoke(main, ["cache", "clear"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload == {"cleared": 1, "freed_bytes": 2}


def test_cache_stats(tmp_path, monkeypatch):
    monkeypatch.setattr("decant.cli.CACHE_DIR", tmp_path)
    (tmp_path / "e.json").write_text("xy")

    result = CliRunner().invoke(main, ["cache", "stats"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["entries"] == 1
    assert payload["total_bytes"] == 2
