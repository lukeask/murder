"""ModeStrip scheduler mode display."""

from __future__ import annotations

from datetime import datetime, timezone

from murder.service.client_api import ScheduleSnapshot
from murder.tui.dispatch.mode_strip import ModeStrip


def _snapshot(*, mode: str = "manual", rationale: str = "") -> ScheduleSnapshot:
    return ScheduleSnapshot(
        scheduler_mode=mode,
        mode_rationale=rationale,
        active_tickets=(),
        recent_done_tickets=(),
        archived_tickets=(),
        scheduler_decisions=(),
        usage_gauges=(),
        calendar_harnesses=(),
        running_agents=(),
        scheduled_tickets=(),
        as_of=datetime.now(timezone.utc),
        invalidation_key="test",
    )


def test_refresh_shows_scheduler_mode_label() -> None:
    strip = ModeStrip()
    strip.refresh_from_snapshot(_snapshot(mode="autorun_ready"))
    assert "Autorun Ready" in str(strip.render())


def test_refresh_shows_crow_magic_rationale() -> None:
    strip = ModeStrip()
    strip.refresh_from_snapshot(
        _snapshot(mode="crow_magic", rationale="waiting for claude quota reset")
    )
    rendered = str(strip.render())
    assert "Crow Magic" in rendered
    assert "waiting for claude quota reset" in rendered
