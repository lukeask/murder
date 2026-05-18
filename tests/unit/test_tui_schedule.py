"""Unit tests for ScheduleTicketsTable binding guards and cursor helpers."""

from __future__ import annotations

import sqlite3

import pytest
from textual.app import App

from murder.tui.dispatch.roster import (
    ScheduleTicketsTable,
    _crow_dispatch_semantics,
    _dispatch_schedule_cell,
    _format_schedule_timestamp,
)
from murder.tui.escalation_strip import EscalationStrip


def _crow_rows(
    conn: sqlite3.Connection,
    *rows: tuple[int, str, str | None],
) -> list[sqlite3.Row]:
    conn.execute("CREATE TEMP TABLE crow_sim (decision INT, rationale TEXT, kicked_ticket_id TEXT)")
    for decision, rationale, kid in rows:
        conn.execute(
            "INSERT INTO crow_sim(decision, rationale, kicked_ticket_id) VALUES (?,?,?)",
            (decision, rationale, kid),
        )
    return conn.execute("SELECT decision, rationale, kicked_ticket_id FROM crow_sim").fetchall()


class _TableMountApp(App):
    def compose(self):
        yield ScheduleTicketsTable()


@pytest.mark.asyncio
async def test_schedule_tickets_table_has_seven_columns_after_mount() -> None:
    app = _TableMountApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.query_one(ScheduleTicketsTable)
        assert table.column_count == 7


def test_format_schedule_timestamp_iso() -> None:
    assert _format_schedule_timestamp("2026-05-16T14:30:00") is not None
    assert "14:30" in (_format_schedule_timestamp("2026-05-16T14:30:00") or "")


def test_dispatch_schedule_done_blank() -> None:
    assert _dispatch_schedule_cell("manual", "done", None, "cursor", "t1", ()) == ""


def test_dispatch_schedule_in_progress_now() -> None:
    assert _dispatch_schedule_cell("manual", "in_progress", None, "cursor", "t1", ()) == "now"


def test_dispatch_schedule_shows_timestamp_over_mode() -> None:
    iso = "2026-05-16T14:30:00+00:00"
    cell = _dispatch_schedule_cell("manual", "planned", iso, "cursor", "t1", ())
    assert cell != "unscheduled"
    assert "14:30" in cell


def test_dispatch_schedule_planned_manual_unscheduled(memdb: sqlite3.Connection) -> None:
    assert _dispatch_schedule_cell("manual", "planned", None, "cursor", "t1", ()) == "unscheduled"


def test_dispatch_schedule_planned_autorun_queued() -> None:
    assert _dispatch_schedule_cell("autorun_ready", "planned", None, "cursor", "t1", ()) == "queued"


def test_crow_semantics_empty_is_queued() -> None:
    assert _crow_dispatch_semantics([], "t1") == "queued"


def test_crow_semantics_holding_is_waiting(memdb: sqlite3.Connection) -> None:
    rows = _crow_rows(memdb, (0, "Holding: cursor/5h usage 12% below threshold 40%", None))
    assert _crow_dispatch_semantics(rows, "t1") == "waiting"


def test_crow_semantics_no_ready_is_queued(memdb: sqlite3.Connection) -> None:
    rows = _crow_rows(
        memdb,
        (0, "No ready tickets for cursor/5h (usage 50% ≥ threshold 40%)", None),
    )
    assert _crow_dispatch_semantics(rows, "t1") == "queued"


def test_crow_semantics_kicked_self_is_queued(memdb: sqlite3.Connection) -> None:
    rows = _crow_rows(memdb, (1, "Kicking t1: cursor/5h usage 50% ≥ threshold 40%", "t1"))
    assert _crow_dispatch_semantics(rows, "t1") == "queued"


def test_dispatch_blocked_no_schedule_blank() -> None:
    assert _dispatch_schedule_cell("manual", "blocked", None, "cursor", "t1", ()) == ""


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
    assert table.cursor_is_editable is False


def test_cursor_is_editable_planned() -> None:
    table = _make_table_with_rows(["planned"])
    assert table.cursor_is_editable is True


def test_cursor_is_editable_ready() -> None:
    table = _make_table_with_rows(["ready"])
    assert table.cursor_is_editable is True


def test_cursor_is_editable_failed() -> None:
    table = _make_table_with_rows(["failed"])
    assert table.cursor_is_editable is True


def test_cursor_is_editable_done_is_true() -> None:
    table = _make_table_with_rows(["done"])
    assert table.cursor_is_editable is True


def test_cursor_is_editable_in_progress_is_true() -> None:
    table = _make_table_with_rows(["in_progress"])
    assert table.cursor_is_editable is True


def test_cursor_is_editable_blocked_is_true() -> None:
    table = _make_table_with_rows(["blocked"])
    assert table.cursor_is_editable is True


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
