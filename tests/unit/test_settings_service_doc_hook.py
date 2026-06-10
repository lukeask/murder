"""SettingsService regenerates HARNESSES_AND_MODELS.md on save (C10 / B9)."""

from __future__ import annotations

import yaml

from murder.app.service.settings_service import ProjectRoleModels, SettingsService
from murder.state.storage.paths import harnesses_and_models_md, roles_yaml
from murder.user_config import UserConfig


def test_save_project_regenerates_doc(tmp_path):
    murder_dir = tmp_path / ".murder"
    murder_dir.mkdir()
    roles_yaml(tmp_path).write_text(yaml.safe_dump({}), encoding="utf-8")

    svc = SettingsService(repo_root=tmp_path)
    result = svc.save_project(
        default_crow={"harness": "claude_code", "model": "sonnet"},
        role_models=ProjectRoleModels(
            crow_handler_model="haiku",
            collaborator_harness="claude_code",
            notetaker_model="llama",
        ),
    )

    assert result.ok, result.error
    assert harnesses_and_models_md(tmp_path).exists()
    text = harnesses_and_models_md(tmp_path).read_text(encoding="utf-8")
    assert "# Harnesses and models" in text
    assert "## claude_code" in text


def test_save_global_regenerates_doc(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    svc = SettingsService(repo_root=tmp_path)
    result = svc.save_global(UserConfig())

    assert result.ok, result.error
    assert harnesses_and_models_md(tmp_path).exists()


def test_save_project_missing_roles_does_not_write_doc(tmp_path):
    # no roles.yaml -> save_project errors before the doc hook
    svc = SettingsService(repo_root=tmp_path)
    result = svc.save_project(
        default_crow={"harness": "claude_code", "model": "sonnet"},
        role_models=ProjectRoleModels(
            crow_handler_model="haiku",
            collaborator_harness="claude_code",
            notetaker_model="llama",
        ),
    )
    assert not result.ok
    assert not harnesses_and_models_md(tmp_path).exists()
