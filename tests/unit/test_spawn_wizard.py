from __future__ import annotations

import asyncio

from murder.app.service.settings_service import ModelDiscoveryResult
from murder.app.tui.spawn_wizard import SpawnWizard, _HARNESS_MODELS, _static_model_ids_for_harness


def test_codex_spawn_models_include_gpt_mini() -> None:
    assert "gpt-5.4-mini" in _HARNESS_MODELS["codex"]


def test_spawn_models_fall_back_to_adapter_startup_models() -> None:
    assert "openai/gpt-5.5" in _static_model_ids_for_harness("pi")
    assert "gpt-5.5" in _static_model_ids_for_harness("cursor")


def test_spawn_wizard_can_select_runtime_discovered_model_harness() -> None:
    async def discover(harness: str) -> ModelDiscoveryResult:
        assert harness == "antigravity"
        return ModelDiscoveryResult(
            ok=True,
            models=(("gemini-3-1-pro", "Gemini 3.1 Pro"),),
        )

    wizard = SpawnWizard(model_discovery=discover)
    wizard._selected_harness = "antigravity"
    wizard._phase = "model_loading"
    wizard._refresh_display = lambda: None  # type: ignore[method-assign]

    assert wizard._should_select_model("antigravity") is True

    asyncio.run(wizard._discover_models("antigravity"))

    assert wizard._phase == "model"
    assert wizard._current_models() == ["gemini-3-1-pro"]
