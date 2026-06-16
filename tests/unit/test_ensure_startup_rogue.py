"""Orchestrator.ensure_startup_rogue — the boot-time auto-spawn of the user's Startup Rogue.

The method is a thin, idempotent wrapper over spawn_rogue keyed off the user-scope
``tui.startup_rogue`` preference: None config = no spawn; a configured rogue spawns once under a
deterministic id and is reused (not re-spawned) when already live.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.user_config import config_path


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    return tmp_path


def _write_startup_rogue(harness: str, model: str, effort: str | None) -> None:
    cfg = config_path()
    cfg.parent.mkdir(parents=True, exist_ok=True)
    effort_line = f"    effort: {effort}\n" if effort is not None else ""
    cfg.write_text(
        "tui:\n  startup_rogue:\n"
        f"    harness: {harness}\n"
        f"    model: {model!r}\n" + effort_line,
        encoding="utf-8",
    )


def _fake_orchestrator(*, existing_agent=None, db_row=None) -> MagicMock:
    orch = MagicMock(spec=Orchestrator)
    orch.rt = MagicMock()
    orch.rt.get_agent = MagicMock(return_value=existing_agent)
    orch.rt.reap = AsyncMock()
    orch.rt.db.execute.return_value.fetchone.return_value = db_row
    orch.spawn_rogue = AsyncMock(return_value="claude-rogue-startup")
    return orch


def test_no_config_returns_none_without_spawning(xdg: Path) -> None:
    orch = _fake_orchestrator()
    result = Orchestrator.ensure_startup_rogue(orch)
    import asyncio

    assert asyncio.run(result) is None
    orch.spawn_rogue.assert_not_awaited()


def test_configured_rogue_spawns_once(xdg: Path) -> None:
    _write_startup_rogue("claude_code", "opus", "medium")
    orch = _fake_orchestrator(existing_agent=None, db_row=None)
    import asyncio

    agent_id = asyncio.run(Orchestrator.ensure_startup_rogue(orch))
    assert agent_id == "claude-rogue-startup"
    orch.spawn_rogue.assert_awaited_once_with("claude_code", "opus", "medium", name="startup")


def test_live_existing_rogue_is_reused_not_respawned(xdg: Path) -> None:
    _write_startup_rogue("claude_code", "opus", "medium")
    live = MagicMock()
    live.is_live = AsyncMock(return_value=True)
    orch = _fake_orchestrator(existing_agent=live)
    import asyncio

    agent_id = asyncio.run(Orchestrator.ensure_startup_rogue(orch))
    # Deterministic id <prefix>-rogue-startup; no re-spawn.
    assert agent_id == "claude-rogue-startup"
    orch.spawn_rogue.assert_not_awaited()
    orch.rt.reap.assert_not_awaited()


def test_dead_existing_rogue_is_reaped_then_respawned(xdg: Path) -> None:
    _write_startup_rogue("claude_code", "", None)
    dead = MagicMock()
    dead.is_live = AsyncMock(return_value=False)
    orch = _fake_orchestrator(existing_agent=dead)
    import asyncio

    asyncio.run(Orchestrator.ensure_startup_rogue(orch))
    orch.rt.reap.assert_awaited_once_with("claude-rogue-startup")
    orch.spawn_rogue.assert_awaited_once_with("claude_code", "", None, name="startup")
