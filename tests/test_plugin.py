"""The repo doubles as a Claude Code plugin; keep its manifests honest."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_plugin_version_matches_pyproject():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["version"] == pyproject["project"]["version"]


def test_every_skill_is_a_skill_md_directory():
    skills = ROOT / "skills"
    assert sorted(p.name for p in skills.iterdir()) == ["decant", "setup"]
    for d in skills.iterdir():
        text = (d / "SKILL.md").read_text()
        assert text.startswith("---\nname: "), d
