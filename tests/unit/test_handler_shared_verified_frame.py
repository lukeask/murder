from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.llm.harness_control.runtime.session import IngestedFrame
from murder.runtime.agents.base import AgentStatus
from murder.runtime.agents.crow_handler import CrowHandler
from murder.runtime.agents.planning_handler import PlanningHandler


@pytest.mark.asyncio
async def test_handlers_share_the_persisted_agent_frame_without_capturing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handler orchestration has one frame provenance and waits for its producer."""

    capture = AsyncMock(side_effect=AssertionError("handler opened a second capture path"))
    monkeypatch.setattr("murder.runtime.terminal.tmux.capture_pane", capture)

    frame = SimpleNamespace(raw_text="shared verified frame")
    evidence = SimpleNamespace(
        payload={
            "transcript": {
                "state": "awaiting_input",
                "segments": [{"type": "assistant", "text": "shared verified frame"}],
            }
        }
    )
    ingested = IngestedFrame(frame=frame, snapshot=MagicMock(), evidence=(evidence,))
    source = SimpleNamespace(latest_ingested_frame=ingested)

    crow_harness = MagicMock()
    crow_harness.is_idle.return_value = True
    crow_harness.detect_done.return_value = False
    crow = CrowHandler.__new__(CrowHandler)
    crow.runtime = SimpleNamespace(
        db=MagicMock(),
        orchestration_events=MagicMock(),
        run_id="run",
        get_crow=lambda _ticket: source,
    )
    crow.ticket_id = "T-1"
    crow.harness = crow_harness
    crow.status = AgentStatus.RUNNING
    crow._idle_cached = False
    crow._on_idle_callbacks = []
    crow._last_pane_hash = None
    crow._done_latched = False
    crow._last_orchestration_t = float("inf")
    crow.config = SimpleNamespace(poll_interval_s=1.0)

    planning_harness = MagicMock()
    planning_harness.detect_answers.return_value = []
    planning = PlanningHandler.__new__(PlanningHandler)
    planning.runtime = SimpleNamespace(get_agent=lambda _agent_id: source)
    planning.plan_name = "p"
    planning.harness = planning_harness
    planning._scan_carve_forms = AsyncMock()

    await crow._tick()
    await planning.tick()

    crow_harness.is_idle.assert_not_called()
    planning_harness.detect_answers.assert_not_called()
    planning._scan_carve_forms.assert_awaited_once_with(frame.raw_text)
    capture.assert_not_awaited()

    source.latest_ingested_frame = None
    crow_harness.reset_mock()
    planning_harness.reset_mock()
    planning._scan_carve_forms.reset_mock()

    await crow._tick()
    await planning.tick()

    crow_harness.is_idle.assert_not_called()
    planning_harness.detect_answers.assert_not_called()
    planning._scan_carve_forms.assert_not_awaited()
    capture.assert_not_awaited()
