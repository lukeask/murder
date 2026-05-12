"""Bottom escalation strip — pending escalations from the DB."""

from __future__ import annotations

import sqlite3

from textual.widgets import Static


class EscalationStrip(Static):
    """Compact text-only list. Empty when no escalations are pending."""

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
        self.border_title = "escalations"

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        rows = db.execute(
            "SELECT id, ticket_id, severity, reason, to_recipient "
            "FROM escalations WHERE resolved = 0 "
            "ORDER BY ts DESC LIMIT 6"
        ).fetchall()
        if not rows:
            self.display = False
            return
        self.display = True
        lines = []
        for r in rows:
            sev = "!" * int(r["severity"])
            tid = r["ticket_id"] or "-"
            lines.append(
                f"[b]{sev}[/b] #{r['id']} → {r['to_recipient']} · {tid} · {r['reason']}"
            )
        self.update("\n".join(lines))
