"""Unit tests for CalendarPanel structure."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from murder.tui.dispatch.calendar import CalendarPanel


class _FakeCalendarPanel(CalendarPanel):
    def __init__(self) -> None:
        self._view_mode = "day"
        self._db = None
        self._harnesses = []
        self._columns = []
        self._rows = []

    def clear(self, columns: bool = False) -> None:
        if columns:
            self._columns = []
        self._rows = []

    def add_column(self, label: str) -> None:
        self._columns.append(label)

    def add_row(self, *cells: object) -> None:
        self._rows.append(cells)


def test_calendar_panel_initial_state() -> None:
    panel = CalendarPanel()
    assert panel._view_mode == "day"
    assert panel._harnesses == []


def test_calendar_panel_refresh_empty_db(memdb: sqlite3.Connection) -> None:
    panel = _FakeCalendarPanel()
    panel.refresh_from_db(memdb)
    assert panel._harnesses == ["default"]
    assert panel._columns == ["Time", "default"]
    assert len(panel._rows) == 24


def test_calendar_panel_refresh_with_harnesses(memdb: sqlite3.Connection) -> None:
    memdb.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) "
        "VALUES ('h1', 's', '2026-05-15', '{}')"
    )
    memdb.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) "
        "VALUES ('h2', 's', '2026-05-15', '{}')"
    )
    panel = _FakeCalendarPanel()
    panel.refresh_from_db(memdb)
    assert panel._harnesses == ["h1", "h2"]
    assert panel._columns == ["Time", "h1", "h2"]
    assert len(panel._rows) == 24


def test_calendar_panel_toggle_view() -> None:
    panel = _FakeCalendarPanel()
    assert panel._view_mode == "day"
    panel.refresh_from_db = lambda db: None  # type: ignore[assignment]
    panel.action_toggle_view()
    assert panel._view_mode == "week"
    panel.action_toggle_view()
    assert panel._view_mode == "day"


def test_calendar_panel_rendering(memdb: sqlite3.Connection) -> None:
    memdb.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) "
        "VALUES ('h1', 's', '2026-05-15', '{}')"
    )
    now = datetime.now(timezone.utc)
    # in-flight agent
    memdb.execute(
        "INSERT INTO tickets(id, title, wave, status, harness, created_at, updated_at) "
        "VALUES ('t1', 'title', 1, 'in_progress', 'h1', '2026-05-15', '2026-05-15')"
    )
    memdb.execute(
        "INSERT INTO agents(agent_id, role, ticket_id, status, started_at) "
        "VALUES ('a1', 'crow', 't1', 'running', ?)",
        (now.isoformat(),),
    )
    # scheduled ticket
    memdb.execute(
        "INSERT INTO tickets(id, title, wave, status, harness, schedule_at, created_at, updated_at) "
        "VALUES ('t2', 'title', 1, 'planned', 'h1', ?, '2026-05-15', '2026-05-15')",
        ((now + timedelta(hours=1)).isoformat(),),
    )

    panel = _FakeCalendarPanel()
    panel.refresh_from_db(memdb)

    # Check that t1 is in the first row (now)
    # and t2 is in the second row (now + 1h)
    assert len(panel._rows) == 24

    # panel._rows[0] is (Time, cell_for_h1)
    # cell_for_h1 is a Text object, we can check its plain string
    assert "t1" in panel._rows[0][1].plain
    assert "t2" in panel._rows[1][1].plain
