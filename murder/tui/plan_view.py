"""Planning-mode widgets."""

from __future__ import annotations

import sqlite3

from textual.message import Message
from textual.widgets import DataTable, Markdown


class PlanList(DataTable):
    """DB-backed plan list."""

    class PlanHighlighted(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    class PlanOpened(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    def __init__(self) -> None:
        super().__init__(zebra_stripes=True, cursor_type="row")
        self._plans: list[str] = []

    def on_mount(self) -> None:
        self.add_columns("name", "status", "rev", "sync")

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        row = self.cursor_row
        rows = db.execute(
            """
            SELECT p.name, p.status, p.sync_state,
                   (SELECT COUNT(*) FROM plan_revisions r WHERE r.plan_name = p.name) AS revisions
              FROM plans p
             ORDER BY p.updated_at DESC, p.name
            """
        ).fetchall()
        self.clear()
        self._plans = []
        for r in rows:
            self.add_row(r["name"], r["status"], str(r["revisions"]), r["sync_state"])
            self._plans.append(r["name"])
        if self._plans:
            self.move_cursor(row=min(max(row, 0), len(self._plans) - 1))

    @property
    def selected_name(self) -> str | None:
        row = self.cursor_row
        if 0 <= row < len(self._plans):
            return self._plans[row]
        return None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._plans):
            self.post_message(self.PlanHighlighted(self._plans[idx]))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._plans):
            self.post_message(self.PlanOpened(self._plans[idx]))


class PlanDocument(Markdown):
    DEFAULT_CSS = """
    PlanDocument {
        border: round $accent;
        height: 1fr;
        width: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self.border_title = "(no plan selected)"

