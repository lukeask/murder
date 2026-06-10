"""Tests for murder.app.tui.spawn_wizard model-discovery async path.

Pins the runtime-discovery contract: when a harness supports discovery,
SpawnWizard drives the async flow, transitions phase to 'model', and
propagates the discovered model list — not the static fallback.
"""

from __future__ import annotations

import asyncio

from murder.app.service.settings_service import ModelDiscoveryResult
from murder.app.tui.spawn_wizard import SpawnWizard


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
