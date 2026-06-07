"""Escalation strip visibility and snapshot rendering."""

from __future__ import annotations

from murder.app.tui.escalation_strip import EscalationStrip
from tests.support.factories import (
    factory_escalation_row,
    factory_escalations_snapshot,
)


def test_refresh_shows_strip_when_active_and_show_true() -> None:
    strip = EscalationStrip()
    strip.refresh_from_snapshot(factory_escalations_snapshot(factory_escalation_row(1)), show=True)
    assert strip.display is True
    assert "[a solve]" in str(strip.render())


def test_refresh_hides_strip_when_show_false() -> None:
    strip = EscalationStrip()
    row = factory_escalation_row(1)
    strip.refresh_from_snapshot(factory_escalations_snapshot(row), show=False)
    assert strip.display is False
    strip.set_user_visible(True)
    assert strip.display is True


def test_set_user_visible_toggles_without_new_snapshot() -> None:
    strip = EscalationStrip()
    row = factory_escalation_row(1, ticket_id=None, reason="kickoff", severity=1)
    strip.refresh_from_snapshot(factory_escalations_snapshot(row))
    strip.set_user_visible(False)
    assert strip.display is False
    strip.set_user_visible(True)
    assert strip.display is True


def test_refresh_hides_history_only_snapshot_and_drops_history_rows() -> None:
    strip = EscalationStrip()
    strip.refresh_from_snapshot(factory_escalations_snapshot(history=(factory_escalation_row(7, reason="old"),)))

    assert strip.display is False
    assert "old" not in str(strip.render())


def test_refresh_with_no_active_clears_stale_retry_target() -> None:
    strip = EscalationStrip()
    strip.refresh_from_snapshot(factory_escalations_snapshot(factory_escalation_row(1, ticket_status="failed")))
    assert strip._latest_failed_ticket_id == "t-1"

    strip.refresh_from_snapshot(factory_escalations_snapshot())

    assert strip.display is False
    assert strip._latest_failed_ticket_id is None


def test_action_ack_posts_selected_escalation_id(monkeypatch) -> None:
    strip = EscalationStrip()
    posted: list[EscalationStrip.AckRequested] = []
    monkeypatch.setattr(strip, "post_message", posted.append)
    strip.refresh_from_snapshot(factory_escalations_snapshot(factory_escalation_row(1), factory_escalation_row(2)))
    strip.action_cursor_down()

    strip.action_ack()

    assert [message.escalation_id for message in posted] == [2]
