"""Bottom escalation strip — pending escalations from the DB."""

from __future__ import annotations

import sqlite3

from textual.binding import Binding
from textual.message import Message
from textual.widgets import Static


class EscalationStrip(Static):
    """Compact text-only list. Empty when no escalations are pending."""

    BINDINGS = [Binding("r", "retry_latest_failed", "Retry failed escalation", show=False)]

    DEFAULT_CSS = """
    EscalationStrip {
        height: auto;
        max-height: 8;
        border: solid $error;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__("escalations: (none)")
        self.can_focus = True
        self.border_title = "escalations"
        self._latest_failed_ticket_id: str | None = None

    class RetryRequested(Message):
        def __init__(self, ticket_id: str) -> None:
            super().__init__()
            self.ticket_id = ticket_id

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        rows = db.execute(
            "SELECT e.id, e.ticket_id, e.severity, e.reason, e.to_recipient, "
            "t.status AS ticket_status "
            "FROM escalations e "
            "LEFT JOIN tickets t ON t.id = e.ticket_id "
            "WHERE e.resolved = 0 "
            "AND (t.status IS NULL OR t.status != 'archived') "
            "ORDER BY e.ts DESC LIMIT 6"
        ).fetchall()
        if not rows:
            self.display = False
            return
        self.display = True
        self._latest_failed_ticket_id = None
        lines = []
        for r in rows:
            sev = "!" * int(r["severity"])
            tid = r["ticket_id"] or "-"
            is_failed = r["ticket_status"] == "failed"
            if is_failed and self._latest_failed_ticket_id is None and r["ticket_id"]:
                self._latest_failed_ticket_id = str(r["ticket_id"])
            retry_hint = " [dim][r retry][/dim]" if is_failed else ""
            lines.append(
                f"[b]{sev}[/b] #{r['id']} → {r['to_recipient']} · {tid} · {r['reason']}"
                + retry_hint
            )
        self.update("\n".join(lines))

    def action_retry_latest_failed(self) -> None:
        if self._latest_failed_ticket_id is None:
            self.app.notify(
                "No failed escalation ticket available to retry.",
                severity="warning",
                timeout=4,
            )
            return
        self.post_message(self.RetryRequested(self._latest_failed_ticket_id))
