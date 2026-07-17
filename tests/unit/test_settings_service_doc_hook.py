"""SettingsService regenerates HARNESSES_AND_MODELS.md on save (C10 / B9)."""

from __future__ import annotations

import asyncio

from murder.app.service.settings_service import SettingsService
from murder.llm.harness_control.runtime.live_model_probe import LiveModelProbeResult
from murder.llm.harnesses.model_cache import CATALOG_ADVISORY, get_available_models
from murder.state.storage.paths import harnesses_and_models_md
from murder.user_config import UserConfig


def test_save_global_regenerates_doc(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    svc = SettingsService(repo_root=tmp_path)
    result = svc.save_global(UserConfig())

    assert result.ok, result.error
    assert harnesses_and_models_md(tmp_path).exists()


def test_discover_models_returns_configured_catalog_with_advisory(tmp_path):
    result = asyncio.run(SettingsService(repo_root=tmp_path).discover_models("claude_code"))

    assert result.ok
    assert result.models == tuple(get_available_models("claude_code"))
    assert result.message == CATALOG_ADVISORY


def test_discover_models_uses_live_verified_probe_for_cursor(tmp_path, monkeypatch):
    calls = []

    async def _probe(kind, cwd):
        calls.append((kind, cwd))
        return LiveModelProbeResult(True, (("gpt-5.6-luna", "GPT-5.6 Luna"),))

    monkeypatch.setattr(
        "murder.app.service.settings_service.probe_live_models",
        _probe,
    )

    result = asyncio.run(SettingsService(repo_root=tmp_path).discover_models("cursor"))

    assert result.ok
    assert result.models == (("gpt-5.6-luna", "GPT-5.6 Luna"),)
    assert result.message is None
    assert calls == [("cursor", tmp_path)]
