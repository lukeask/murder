"""Calendar panel — shows in-flight and scheduled tickets."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from rich.text import Text
from textual.binding import Binding
from textual.widgets import DataTable

from murder.app.service.client_api import (
    CalendarRunningAgent,
    CalendarScheduledTicket,
    ScheduleSnapshot,
)
from murder.app.tui.components import StoreComponent


class CalendarPanel(StoreComponent, DataTable):
    """In-flight and user-scheduled ticket calendar.

    Parent-cascade pattern: DispatchView is bound to the schedule store and
    forwards the snapshot via refresh_from_snapshot().  This widget is NOT
    independently bound; it renders on demand from the parent cascade.
    """

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
        DataTable.__init__(self, id="calendar_panel", zebra_stripes=True, cursor_type="cell")
        self._view_mode: str = "day"
        self._snapshot: Any | None = None

    def on_mount(self) -> None:
        self.add_column("Time")
        super().on_mount()  # StoreComponent subscribes if bound

    def action_toggle_view(self) -> None:
        self._view_mode = "week" if self._view_mode == "day" else "day"
        if self._snapshot is not None:
            self.refresh_from_snapshot(self._snapshot)

    def action_jump_now(self) -> None:
        self.move_cursor(row=0)

    def action_jump_end(self) -> None:
        if self.row_count > 0:
            self.move_cursor(row=self.row_count - 1)

    def refresh_from_snapshot(self, snapshot: Any) -> None:
        """Accepts both ScheduleSnapshot (bridge) and ScheduleStoreSnapshot (self-subscribe)."""
        self._snapshot = snapshot
        harnesses = list(snapshot.calendar_harnesses) or ["default"]
        self.clear(columns=True)
        self.add_column("Time")
        for h in harnesses:
            self.add_column(h)

        now = datetime.now(timezone.utc)
        if self._view_mode == "day":
            start_time = now.replace(minute=0, second=0, microsecond=0)
            intervals = [start_time + timedelta(hours=i) for i in range(24)]
            time_format = "%H:00"
            delta = timedelta(hours=1)
        else:
            start_time = now.replace(hour=0, minute=0, second=0, microsecond=0)
            intervals = [start_time + timedelta(days=i) for i in range(7)]
            time_format = "%a %d"
            delta = timedelta(days=1)

        for t in intervals:
            end_t = t + delta
            row_cells = [Text(t.strftime(time_format))]
            for h in harnesses:
                cell_text = Text()
                for agent in snapshot.running_agents:
                    if _agent_in_cell(agent, harness=h, interval_start=t, interval_end=end_t, now=now):
                        if len(cell_text) > 0:
                            cell_text.append("\n")
                        cell_text.append(f"▶ {agent.ticket_id}", style="green")
                for ticket in snapshot.scheduled_tickets:
                    if _scheduled_in_cell(ticket, harness=h, interval_start=t, interval_end=end_t):
                        if len(cell_text) > 0:
                            cell_text.append("\n")
                        cell_text.append(f"○ {ticket.ticket_id}", style="yellow")
                row_cells.append(cell_text)
            self.add_row(*row_cells)

def _agent_in_cell(
    agent: CalendarRunningAgent,
    *,
    harness: str,
    interval_start: datetime,
    interval_end: datetime,
    now: datetime,
) -> bool:
    if (agent.harness or "default") != harness:
        return False
    try:
        started_at = datetime.fromisoformat(agent.started_at)
    except ValueError:
        return False
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return started_at < interval_end and now >= interval_start


def _scheduled_in_cell(
    ticket: CalendarScheduledTicket,
    *,
    harness: str,
    interval_start: datetime,
    interval_end: datetime,
) -> bool:
    if (ticket.harness or "default") != harness:
        return False
    try:
        sched_at = datetime.fromisoformat(ticket.schedule_at)
    except ValueError:
        return False
    if sched_at.tzinfo is None:
        sched_at = sched_at.replace(tzinfo=timezone.utc)
    return interval_start <= sched_at < interval_end
