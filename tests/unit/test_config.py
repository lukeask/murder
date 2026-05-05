"""Config loader: bundled defaults + project override + .env."""

from __future__ import annotations

import os

import pytest


def test_load_reads_project_env_created_under_agents(tmp_path, monkeypatch) -> None:
    from murder.config import Config, project_env_path

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    project_env_path(tmp_path).parent.mkdir()
    project_env_path(tmp_path).write_text("OPENROUTER_API_KEY=from_agents\n", encoding="utf-8")

    Config.load(tmp_path)

    assert os.environ["OPENROUTER_API_KEY"] == "from_agents"


def test_root_env_overrides_agents_env(tmp_path, monkeypatch) -> None:
    from murder.config import Config, project_env_path

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    project_env_path(tmp_path).parent.mkdir()
    project_env_path(tmp_path).write_text("OPENROUTER_API_KEY=from_agents\n", encoding="utf-8")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=from_root\n", encoding="utf-8")

    Config.load(tmp_path)

    assert os.environ["OPENROUTER_API_KEY"] == "from_root"


def test_init_creates_project_env_that_config_loads(tmp_path, monkeypatch) -> None:
    from murder.cli import cmd_init
    from murder.config import Config, project_env_path

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "from_process")

    cmd_init()
    assert "# OPENROUTER_API_KEY=" in project_env_path(tmp_path).read_text(encoding="utf-8")
    Config.load(tmp_path)
    assert os.environ["OPENROUTER_API_KEY"] == "from_process"

    project_env_path(tmp_path).write_text("OPENROUTER_API_KEY=from_init\n", encoding="utf-8")
    Config.load(tmp_path)

    assert os.environ["OPENROUTER_API_KEY"] == "from_init"


def test_default_tui_refresh_is_1000ms() -> None:
    """D11."""
    from murder.config import TuiConfig

    assert TuiConfig().refresh_ms == 1000


def test_load_with_no_project_yaml_uses_bundled_defaults(tmp_path) -> None:
    # TODO(M0): create tmp_path/.agents/; do not write roles.yaml; Config.load(tmp_path).
    pytest.skip("M0 stub")


def test_invalid_yaml_fails_loud(tmp_path) -> None:
    # TODO(M0): write malformed roles.yaml; Config.load raises with field path.
    pytest.skip("M0 stub")
