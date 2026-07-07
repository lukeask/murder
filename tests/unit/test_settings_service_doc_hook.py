"""SettingsService regenerates HARNESSES_AND_MODELS.md on save (C10 / B9)."""

from __future__ import annotations

from murder.app.service.settings_service import SettingsService
from murder.state.storage.paths import harnesses_and_models_md
from murder.user_config import UserConfig


def test_save_global_regenerates_doc(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    svc = SettingsService(repo_root=tmp_path)
    result = svc.save_global(UserConfig())

    assert result.ok, result.error
    assert harnesses_and_models_md(tmp_path).exists()
