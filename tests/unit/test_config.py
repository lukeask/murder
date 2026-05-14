"""Config loader: bundled defaults + project override + .env."""

from __future__ import annotations

import os

import pytest
import yaml


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


def test_load_with_no_user_no_project_matches_bundled_only(tmp_path, monkeypatch) -> None:
    """Missing ~/.config/murder/config.yaml is a silent no-op for role merge."""
    from murder.config import Config, _load_bundled_defaults

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    expected = Config.model_validate(_load_bundled_defaults())
    assert Config.load(tmp_path).model_dump() == expected.model_dump()


def test_user_config_collaborator_harness_no_project(tmp_path, monkeypatch) -> None:
    from murder.config import Config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    murder_d = tmp_path / "xdg" / "murder"
    murder_d.mkdir(parents=True)
    (murder_d / "config.yaml").write_text(
        yaml.safe_dump({"collaborator": {"harness": "codex"}}),
        encoding="utf-8",
    )

    cfg = Config.load(tmp_path)
    assert cfg.collaborator.harness == "codex"


def test_project_roles_yaml_overrides_user_config_collaborator(tmp_path, monkeypatch) -> None:
    from murder.config import Config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    murder_d = tmp_path / "xdg" / "murder"
    murder_d.mkdir(parents=True)
    (murder_d / "config.yaml").write_text(
        yaml.safe_dump({"collaborator": {"harness": "codex"}}),
        encoding="utf-8",
    )
    proj = tmp_path / ".murder"
    proj.mkdir()
    (proj / "roles.yaml").write_text(
        yaml.safe_dump({"collaborator": {"harness": "cursor"}}),
        encoding="utf-8",
    )

    assert Config.load(tmp_path).collaborator.harness == "cursor"


def test_user_config_path_follows_xdg_config_home_for_role_merge(tmp_path, monkeypatch) -> None:
    """User-level overrides are read from XDG_CONFIG_HOME/murder/config.yaml."""
    from murder.config import Config

    xdg = tmp_path / "customxdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    murder_d = xdg / "murder"
    murder_d.mkdir(parents=True)
    (murder_d / "config.yaml").write_text(
        yaml.safe_dump({"notetaker": {"model": "user/model-id"}}),
        encoding="utf-8",
    )

    assert Config.load(tmp_path).notetaker.model == "user/model-id"


def test_user_config_round_trip_survives_schema_extension(tmp_path) -> None:
    from murder.user_config import (
        UserConfig,
        UserHarnessRolePatch,
        UserNotetakerPatch,
        load_user_config,
        save_user_config,
    )

    path = tmp_path / "murder" / "config.yaml"
    path.parent.mkdir(parents=True)
    original = UserConfig()
    original.tui.theme = "everforest-dark-hard"
    original.collaborator = UserHarnessRolePatch(harness="codex")
    original.default_crow = UserHarnessRolePatch(startup_model="gpt-4.1")
    original.notetaker = UserNotetakerPatch(model="anthropic/claude-opus-4-1")

    save_user_config(original, path)
    assert load_user_config(path) == original


def test_invalid_yaml_fails_loud(tmp_path) -> None:
    # TODO(M0): write malformed roles.yaml; Config.load raises with field path.
    pytest.skip("M0 stub")
