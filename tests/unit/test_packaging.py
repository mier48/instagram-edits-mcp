"""Manifests, version numbers, launcher and example configuration stay consistent."""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

from instagram_edits_mcp import __version__
from instagram_edits_mcp.config import load_config

ROOT = Path(__file__).resolve().parents[2]


def manifest() -> dict[str, object]:
    return json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())


def test_single_version_and_no_runtime_dependencies() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["version"] == manifest()["version"] == __version__
    assert project["requires-python"] == ">=3.12" and project["dependencies"] == []


def test_plugin_uses_the_isolated_launcher_and_a_sensitive_token() -> None:
    plugin = manifest()
    server = plugin["mcpServers"]["instagram-edits"]  # type: ignore[index]
    assert server["command"] == "${CLAUDE_PLUGIN_ROOT}/bin/instagram-edits-mcp"
    assert "PYTHONPATH" not in server["env"]
    assert plugin["userConfig"]["edits_token"]["sensitive"] is True  # type: ignore[index]
    launcher = ROOT / "bin" / "instagram-edits-mcp"
    assert os.access(launcher, os.X_OK) and '"$py" -I -c' in launcher.read_text()


def test_example_configuration_loads(tmp_path: Path) -> None:
    target = tmp_path / "config.toml"
    target.write_text((ROOT / "examples" / "config.toml").read_text())
    target.chmod(0o600)
    config = load_config({"INSTAGRAM_EDITS_MCP_CONFIG": str(target)})
    assert config.deny == ("deleteProject",) and config.verify == "enforce"
