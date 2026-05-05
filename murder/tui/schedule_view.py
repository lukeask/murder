"""Usage-aware schedule and queue view."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from textual.widgets import Static


class ScheduleView(Static):
    """Command-center view for ready work, planned starts, and usage windows."""

    DEFAULT_CSS = """
    ScheduleView {
        border: round $accent;
        height: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self.border_title = "schedule"

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        ready = db.execute(
            """
            SELECT id, title, harness, model, updated_at
              FROM tickets
             WHERE status = 'ready'
             ORDER BY wave, updated_at DESC, id
             LIMIT 10
            """
        ).fetchall()
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
            "[b]Usage-aware queue[/b]",
            "WIP: open TUI will own scheduled launches; runner thresholds are not wired yet.",
            "TODO: collect harness usage snapshots on cadence and trigger due schedule_queue rows.",
            "",
            "[b]Ready tickets[/b]",
        ]
        if ready:
            for r in ready:
                harness = r["harness"] or "default"
                model = f" · {r['model']}" if r["model"] else ""
                lines.append(f"  {r['id']} · {harness}{model} · {r['title']}")
        else:
            lines.append("  (none)")

        lines.extend(["", "[b]Scheduled / pending[/b]"])
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
            lines.append("  (empty; focus chat with F2, then type /murder to kick ready tickets)")

        lines.extend(["", "[b]Latest usage windows[/b]"])
        if usage:
            for r in usage:
                lines.extend(_usage_lines(dict(r)))
        else:
            lines.append("  (no snapshots yet)")
            lines.append("  TODO: persist HarnessUsageStatus from Claude/Codex/Cursor facade.")

        self.update("\n".join(lines))


def _format_start(value: str | None) -> str:
    if not value:
        return "unscheduled"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return dt.strftime("%a %H:%M")


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
