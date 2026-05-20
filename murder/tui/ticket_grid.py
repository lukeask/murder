"""Left-side ticket list — live DataTable backed by the SQLite tickets table."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import DataTable

from murder.service.client_api import DispatchSnapshot


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

    def refresh_from_snapshot(self, snapshot: DispatchSnapshot) -> None:
        self.clear()
        self._tickets = []
        for ticket in snapshot.tickets:
            self.add_row(
                ticket.id,
                str(ticket.wave),
                ticket.status.value,
                ticket.title,
            )
            self._tickets.append(ticket.id)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._tickets):
            self.post_message(self.TicketSelected(self._tickets[idx]))
