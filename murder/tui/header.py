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
        self._view = "planning"
        self._planning_mode: str | None = None

    def refresh_counts(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        for s in _STATUSES:
            row = db.execute(
                "SELECT COUNT(*) AS c FROM tickets WHERE status = ?", (s,)
            ).fetchone()
            self._counts[s] = int(row["c"]) if row else 0
        self._update_text()

    def set_view(self, view: str, planning_mode: str | None = None) -> None:
        self._view = view
        self._planning_mode = planning_mode
        self._update_text()

    def _update_text(self) -> None:
        parts = " ".join(f"{s}:{self._counts[s]}" for s in _STATUSES)
        project = (
            "[red][unconfigured][/red]"
            if self.project == "TODO_SET_ME"
            else self.project
        )
        planning_label = "[1 planning]"
        if self._planning_mode:
            planning_label = f"[1 planning · {self._planning_mode}]"
        tabs = " ".join(
            f"[b]{label}[/b]" if key == self._view else label
            for key, label in (
                ("planning", planning_label),
                ("crows", "[2 crows]"),
                ("schedule", "[3 schedule]"),
            )
        )
        self.update(f"[b]murder[/b] · {project} · {tabs} · {parts}")
