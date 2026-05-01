"""Header bar: project name + ticket status counts."""

from __future__ import annotations

import sqlite3

from textual.widgets import Static

_STATUSES = ("planned", "ready", "in_progress", "blocked", "done", "failed")


class Header(Static):
    """Project name on the left, live status counts on the right."""

    DEFAULT_CSS = """
    Header {
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self, project: str) -> None:
        super().__init__("murder")
        self.project = project
        self._counts: dict[str, int] = {s: 0 for s in _STATUSES}

    def refresh_counts(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        for s in _STATUSES:
            row = db.execute(
                "SELECT COUNT(*) AS c FROM tickets WHERE status = ?", (s,)
            ).fetchone()
            self._counts[s] = int(row["c"]) if row else 0
        self._update_text()

    def _update_text(self) -> None:
        parts = " ".join(f"{s}:{self._counts[s]}" for s in _STATUSES)
        self.update(f"[b]murder[/b] · {self.project} · {parts}")
