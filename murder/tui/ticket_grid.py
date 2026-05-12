"""Left-side ticket list — live DataTable backed by the SQLite tickets table."""

from __future__ import annotations

import sqlite3

from textual.message import Message
from textual.widgets import DataTable


class TicketGrid(DataTable):
    """Rows: id, wave, status, title. Selecting a row emits TicketSelected."""

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    class TicketSelected(Message):
        def __init__(self, ticket_id: str) -> None:
            self.ticket_id = ticket_id
            super().__init__()

    def __init__(self) -> None:
        super().__init__(zebra_stripes=True, cursor_type="row")
        self._tickets: list[str] = []

    def on_mount(self) -> None:
        self.add_columns("id", "wave", "status", "title")

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        rows = db.execute(
            "SELECT id, title, wave, status FROM tickets ORDER BY wave, id"
        ).fetchall()
        self.clear()
        self._tickets = []
        for r in rows:
            self.add_row(r["id"], str(r["wave"]), r["status"], r["title"])
            self._tickets.append(r["id"])

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._tickets):
            self.post_message(self.TicketSelected(self._tickets[idx]))
