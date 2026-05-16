"""DispatchView — composes the ticket roster, mode strip, gauges, and calendar."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Static

from murder.tui.dispatch.calendar import CalendarPanel
from murder.tui.dispatch.gauges import GaugeStrip
from murder.tui.dispatch.mode_strip import ModeStrip
from murder.tui.dispatch.roster import ScheduleTicketsTable, _format_start


class DispatchView(Vertical):
    """Command-centre: mode strip, ticket roster, usage, gauges, and calendar."""

    DEFAULT_CSS = """
    DispatchView {
        border: round $accent;
        height: 1fr;
        padding: 0 1;
    }
    DispatchView #dispatch_body {
        height: auto;
        min-height: 6;
        max-height: 18;
        margin-bottom: 1;
    }
    DispatchView #schedule_tickets {
        width: 2fr;
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
    DispatchView #schedule_rest {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield ModeStrip()
        yield GaugeStrip()
        with Horizontal(id="dispatch_body"):
            yield ScheduleTicketsTable()
            yield CalendarPanel()
        yield Static("", id="schedule_rest")

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        self.query_one(ModeStrip).refresh_from_db(db)
        self.query_one(GaugeStrip).refresh_from_db(db)
        self.query_one(ScheduleTicketsTable).refresh_from_db(db)
        self.query_one("#schedule_rest", Static).update(_dispatch_tail_content(db))

    @property
    def selected_ticket_id(self) -> str | None:
        return self.query_one(ScheduleTicketsTable).cursor_ticket_id

    @property
    def selected_ticket_is_editable(self) -> bool:
        return self.query_one(ScheduleTicketsTable).cursor_is_editable


def _dispatch_tail_content(db: sqlite3.Connection | None) -> str:
    if db is None:
        return ""
    queued = db.execute(
        """
        SELECT id, ticket_id, title, harness, desired_start_at,
               max_usage_percent, status
          FROM schedule_queue
         WHERE status IN ('pending','scheduled','blocked')
         ORDER BY
               CASE WHEN desired_start_at IS NULL THEN 1 ELSE 0 END,
               desired_start_at,
               id
         LIMIT 10
        """
    ).fetchall()
    usage = db.execute(
        """
        SELECT s.harness, s.source, s.fetched_at, s.status_json
          FROM harness_usage_snapshots s
          JOIN (
                SELECT harness, MAX(fetched_at) AS fetched_at
                  FROM harness_usage_snapshots
                 GROUP BY harness
               ) latest
            ON latest.harness = s.harness
           AND latest.fetched_at = s.fetched_at
         ORDER BY s.harness
        """
    ).fetchall()

    lines = [
        "[b]Dispatch[/b] — [b]ready[/b] rows with deps ok kick with F6; "
        "[b]c[/b] / Enter edits YAML metadata.",
        "",
        "[b]Scheduled / pending[/b]",
    ]
    if queued:
        for r in queued:
            start = _format_start(r["desired_start_at"])
            cap = (
                f" · start if usage <= {r['max_usage_percent']:.0f}%"
                if r["max_usage_percent"] is not None
                else ""
            )
            ticket = f"{r['ticket_id']} · " if r["ticket_id"] else ""
            lines.append(
                f"  #{r['id']} · {r['status']} · {start} · "
                f"{r['harness'] or 'default'}{cap} · {ticket}{r['title']}"
            )
    else:
        lines.append(
            "  (empty; scheduling not wired — use F6 or /murder to kick ready tickets)"
        )

    lines.extend(["", "[b]Latest usage windows[/b]"])
    if usage:
        for r in usage:
            lines.extend(_usage_lines(dict(r)))
    else:
        lines.append("  (no snapshots yet; press u to sample)")
        lines.append("  Probe tmux sessions: murder_<project>_usage_<harness>")

    return "\n".join(lines)


def _usage_lines(row: dict[str, Any]) -> list[str]:
    try:
        payload = json.loads(row["status_json"])
    except (TypeError, ValueError):
        return [f"  {row['harness']} · {row['source']} · malformed snapshot"]
    windows = payload.get("windows") or []
    if not isinstance(windows, list) or not windows:
        return [f"  {row['harness']} · {row['source']} · no windows"]
    out = [f"  {row['harness']} · {row['source']} · fetched {row['fetched_at']}"]
    for window in windows[:4]:
        if not isinstance(window, dict):
            continue
        name = window.get("name") or "usage"
        pct = window.get("percent_used")
        reset = window.get("reset_at") or window.get("ends_at") or "unknown reset"
        pct_text = f"{pct:.0f}%" if isinstance(pct, (int, float)) else "unknown"
        out.append(f"    {name}: {pct_text} used · reset {reset}")
    return out
