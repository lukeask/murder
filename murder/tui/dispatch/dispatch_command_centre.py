"""DispatchView — composes the ticket roster, mode strip, gauges, and calendar."""

from __future__ import annotations

import sqlite3

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical

from murder.tui.dispatch.calendar import CalendarPanel
from murder.tui.dispatch.gauges import GaugeStrip
from murder.tui.dispatch.mode_strip import ModeStrip
from murder.tui.dispatch.roster import ScheduleTicketsTable


class DispatchView(Vertical):
    """Command-centre: mode strip, ticket roster, usage, gauges, and calendar."""

    DEFAULT_CSS = """
    DispatchView {
        border: round $accent;
        height: 1fr;
        padding: 0 1;
    }
    DispatchView #dispatch_body {
        height: 1fr;
        margin-bottom: 1;
    }
    DispatchView #dispatch_body {
        min-width: 72;
    }
    DispatchView #schedule_tickets {
        width: 2fr;
        min-width: 72;
        height: 100%;
    }
    DispatchView CalendarPanel {
        width: 1fr;
        height: 100%;
        margin-left: 1;
    }
    DispatchView #field_deps,
    DispatchView #field_writes,
    DispatchView #field_skills,
    DispatchView #field_checklist {
        height: 4;
        min-height: 3;
    }
    """

    def compose(self) -> ComposeResult:
        yield ModeStrip()
        yield GaugeStrip()
        with Horizontal(id="dispatch_body"):
            yield ScheduleTicketsTable()
            yield CalendarPanel()

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        self.query_one(ModeStrip).refresh_from_db(db)
        self.query_one(GaugeStrip).refresh_from_db(db)
        self.query_one(ScheduleTicketsTable).refresh_from_db(db)
        self.query_one(CalendarPanel).refresh_from_db(db)

    @property
    def selected_ticket_id(self) -> str | None:
        return self.query_one(ScheduleTicketsTable).cursor_ticket_id
