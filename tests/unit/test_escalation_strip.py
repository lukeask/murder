"""Escalation strip visibility and snapshot rendering."""

from __future__ import annotations

from datetime import datetime, timezone

from murder.service.client_api import EscalationsSnapshot, EscalationSummary
from murder.tui.escalation_strip import EscalationStrip


def _row(
    escalation_id: int,
    *,
    ticket_id: str | None = "t-1",
    reason: str = "blocked",
    severity: int = 2,
    to_recipient: str = "user",
    ticket_status: str | None = None,
) -> EscalationSummary:
    return EscalationSummary(
        id=escalation_id,
        ticket_id=ticket_id,
        severity=severity,
        reason=reason,
        to_recipient=to_recipient,
        body_path=None,
        ticket_status=ticket_status,
    )


def _snapshot(
    *active: EscalationSummary,
    history: tuple[EscalationSummary, ...] = (),
) -> EscalationsSnapshot:
    return EscalationsSnapshot(
        active=active,
        history=history,
        as_of=datetime.now(timezone.utc),
        invalidation_key="test",
    )


def test_refresh_shows_strip_when_active_and_show_true() -> None:
    strip = EscalationStrip()
    strip.refresh_from_snapshot(_snapshot(_row(1)), show=True)
    assert strip.display is True
    assert "[a solve]" in str(strip.render())


def test_refresh_hides_strip_when_show_false() -> None:
    strip = EscalationStrip()
    row = _row(1)
    strip.refresh_from_snapshot(_snapshot(row), show=False)
    assert strip.display is False
    strip.set_user_visible(True)
    assert strip.display is True


def test_set_user_visible_toggles_without_new_snapshot() -> None:
    strip = EscalationStrip()
    row = _row(1, ticket_id=None, reason="kickoff", severity=1)
    strip.refresh_from_snapshot(_snapshot(row))
    strip.set_user_visible(False)
    assert strip.display is False
    strip.set_user_visible(True)
    assert strip.display is True


def test_refresh_hides_history_only_snapshot_and_drops_history_rows() -> None:
    strip = EscalationStrip()
    strip.refresh_from_snapshot(_snapshot(history=(_row(7, reason="old"),)))

    assert strip.display is False
    assert "old" not in str(strip.render())


def test_refresh_with_no_active_clears_stale_retry_target() -> None:
    strip = EscalationStrip()
    strip.refresh_from_snapshot(_snapshot(_row(1, ticket_status="failed")))
    assert strip._latest_failed_ticket_id == "t-1"

    strip.refresh_from_snapshot(_snapshot())

    assert strip.display is False
    assert strip._latest_failed_ticket_id is None


def test_action_ack_posts_selected_escalation_id(monkeypatch) -> None:
    strip = EscalationStrip()
    posted: list[EscalationStrip.AckRequested] = []
    monkeypatch.setattr(strip, "post_message", posted.append)
    strip.refresh_from_snapshot(_snapshot(_row(1), _row(2)))
    strip.action_cursor_down()

    strip.action_ack()

    assert [message.escalation_id for message in posted] == [2]
