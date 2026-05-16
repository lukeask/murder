"""Calendar panel — shows in-flight and scheduled tickets."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from rich.text import Text
from textual.binding import Binding
from textual.widgets import DataTable


class CalendarPanel(DataTable):
    """In-flight and user-scheduled ticket calendar."""

    DEFAULT_CSS = """
    CalendarPanel {
        height: 100%;
        border: solid $primary;
    }
    """

    BINDINGS = [
        Binding("z", "toggle_view", "Toggle View (Day/Week)"),
        Binding("g", "jump_now", "Jump to Now"),
        Binding("G", "jump_end", "Jump to End"),
    ]

    def __init__(self) -> None:
        super().__init__(id="calendar_panel", zebra_stripes=True, cursor_type="cell")
        self._view_mode: str = "day"  # 'day' or 'week'
        self._db: sqlite3.Connection | None = None
        self._harnesses: list[str] = []

    def on_mount(self) -> None:
        self.add_column("Time")

    def action_toggle_view(self) -> None:
        self._view_mode = "week" if self._view_mode == "day" else "day"
        self.refresh_from_db(self._db)

    def action_jump_now(self) -> None:
        self.move_cursor(row=0)
        self.scroll_to_row(0)

    def action_jump_end(self) -> None:
        if self.row_count > 0:
            self.move_cursor(row=self.row_count - 1)
            self.scroll_to_row(self.row_count - 1)

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        self._db = db
        if db is None:
            return

        # Get harnesses from usage snapshots
        harness_rows = db.execute(
            "SELECT DISTINCT harness FROM harness_usage_snapshots ORDER BY harness"
        ).fetchall()
        self._harnesses = [r["harness"] for r in harness_rows]
        if not self._harnesses:
            self._harnesses = ["default"]

        self.clear(columns=True)
        self.add_column("Time")
        for h in self._harnesses:
            self.add_column(h)

        # Get in-flight agents
        agents = db.execute(
            """
            SELECT a.agent_id, a.ticket_id, a.started_at, t.harness
              FROM agents a
              JOIN tickets t ON t.id = a.ticket_id
             WHERE a.status = 'running'
            """
        ).fetchall()

        # Get scheduled tickets
        scheduled = db.execute(
            """
            SELECT id AS ticket_id, schedule_at, harness
              FROM tickets
             WHERE schedule_at IS NOT NULL
               AND status IN ('planned', 'ready', 'blocked')
            """
        ).fetchall()

        # Build grid
        now = datetime.now(timezone.utc)
        
        if self._view_mode == "day":
            # 24 hours starting from current hour
            start_time = now.replace(minute=0, second=0, microsecond=0)
            intervals = [start_time + timedelta(hours=i) for i in range(24)]
            time_format = "%H:00"
            delta = timedelta(hours=1)
        else:
            # 7 days starting from today
            start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            intervals = [start_time + timedelta(days=i) for i in range(7)]
            time_format = "%a %d"
            delta = timedelta(days=1)

        for t in intervals:
            end_t = t + delta
            row_cells = [Text(t.strftime(time_format))]
            for h in self._harnesses:
                cell_text = Text()
                # Check in-flight
                for a in agents:
                    if (a["harness"] or "default") == h:
                        try:
                            started_at = datetime.fromisoformat(a["started_at"])
                            if started_at.tzinfo is None:
                                started_at = started_at.replace(tzinfo=timezone.utc)
                            if started_at < end_t and now >= t:
                                if len(cell_text) > 0:
                                    cell_text.append("\n")
                                cell_text.append(f"▶ {a['ticket_id']}", style="green")
                        except ValueError:
                            pass
                
                # Check scheduled
                for s in scheduled:
                    if (s["harness"] or "default") == h:
                        try:
                            sched_at = datetime.fromisoformat(s["schedule_at"])
                            if sched_at.tzinfo is None:
                                sched_at = sched_at.replace(tzinfo=timezone.utc)
                            if t <= sched_at < end_t:
                                if len(cell_text) > 0:
                                    cell_text.append("\n")
                                cell_text.append(f"○ {s['ticket_id']}", style="yellow")
                        except ValueError:
                            pass
                
                row_cells.append(cell_text)
            
            self.add_row(*row_cells)
