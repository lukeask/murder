"""Unit tests for ScheduleTicketsTable binding guards and cursor helpers."""

from __future__ import annotations

import sqlite3

from murder.tui.escalation_strip import EscalationStrip
from murder.tui.dispatch.roster import ScheduleTicketsTable


class _FakeTable(ScheduleTicketsTable):
    """Minimal stand-in — no Textual app needed."""

    def __init__(self, statuses: list[str], cursor: int = 0) -> None:
        # Bypass DataTable.__init__ which requires an app loop.
        object.__setattr__(self, "_ids", [f"t{i:03d}" for i in range(len(statuses))])
        object.__setattr__(self, "_statuses", list(statuses))
        object.__setattr__(self, "_cursor", cursor)

    @property  # type: ignore[override]
    def cursor_row(self) -> int:  # type: ignore[override]
        return object.__getattribute__(self, "_cursor")


def _make_table_with_rows(statuses: list[str], cursor: int = 0) -> _FakeTable:
    return _FakeTable(statuses, cursor)


def test_cursor_status_returns_current_row_status() -> None:
    table = _make_table_with_rows(["planned", "failed", "done"], cursor=1)
    assert table.cursor_status == "failed"


def test_cursor_status_empty_table() -> None:
    table = _make_table_with_rows([])
    assert table.cursor_status is None


def test_cursor_is_editable_planned() -> None:
    table = _make_table_with_rows(["planned"])
    assert table.cursor_is_editable is True


def test_cursor_is_editable_ready() -> None:
    table = _make_table_with_rows(["ready"])
    assert table.cursor_is_editable is True


def test_cursor_is_editable_failed() -> None:
    table = _make_table_with_rows(["failed"])
    assert table.cursor_is_editable is True


def test_cursor_is_editable_done_is_false() -> None:
    table = _make_table_with_rows(["done"])
    assert table.cursor_is_editable is False


def test_cursor_is_editable_in_progress_is_false() -> None:
    table = _make_table_with_rows(["in_progress"])
    assert table.cursor_is_editable is False


def test_r_binding_declared() -> None:
    binding_keys = {b.key for b in ScheduleTicketsTable.BINDINGS}
    assert "r" in binding_keys


def test_retry_requested_message_carries_ticket_id() -> None:
    msg = ScheduleTicketsTable.RetryRequested("t042")
    assert msg.ticket_id == "t042"


def test_escalation_strip_declares_r_binding() -> None:
    binding_keys = {b.key for b in EscalationStrip.BINDINGS}
    assert "r" in binding_keys


def test_escalation_strip_tracks_latest_failed_ticket(memdb: sqlite3.Connection) -> None:
    memdb.execute(
        "INSERT INTO tickets(id, title, wave, status, attempts, created_at, updated_at) "
        "VALUES ('t100', 'x', 1, 'failed', 0, '2024-01-01', '2024-01-01')"
    )
    memdb.execute(
        "INSERT INTO escalations(ticket_id, severity, reason, to_recipient, resolved, ts) "
        "VALUES ('t100', 2, 'oops', 'user', 0, '2024-01-02T00:00:00')"
    )
    strip = EscalationStrip()
    strip.refresh_from_db(memdb)
    assert strip._latest_failed_ticket_id == "t100"


def test_escalation_strip_retry_posts_message() -> None:
    strip = EscalationStrip()
    strip._latest_failed_ticket_id = "t042"
    captured: list[str] = []

    def _capture(msg: object) -> None:
        ticket_id = getattr(msg, "ticket_id", None)
        if isinstance(ticket_id, str):
            captured.append(ticket_id)

    strip.post_message = _capture  # type: ignore[assignment]
    strip.action_retry_latest_failed()
    assert captured == ["t042"]
