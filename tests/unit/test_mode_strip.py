"""ModeStrip scheduler mode display."""

from __future__ import annotations

from murder.app.tui.dispatch.mode_strip import ModeStrip
from tests.support.factories import factory_schedule_snapshot


def test_refresh_shows_scheduler_mode_label() -> None:
    strip = ModeStrip()
    strip.refresh_from_snapshot(factory_schedule_snapshot(scheduler_mode="autorun_ready"))
    assert "Autorun Ready" in str(strip.render())


def test_refresh_shows_crow_magic_rationale() -> None:
    strip = ModeStrip()
    strip.refresh_from_snapshot(
        factory_schedule_snapshot(scheduler_mode="crow_magic", mode_rationale="waiting for claude quota reset")
    )
    rendered = str(strip.render())
    assert "Crow Magic" in rendered
    assert "waiting for claude quota reset" in rendered
