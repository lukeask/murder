"""Left-side ticket list — live DataTable backed by the SQLite tickets table."""

from __future__ import annotations

from textual.message import Message
from textual.widgets import DataTable

from murder.app.service.client_api import DispatchSnapshot
from murder.app.tui.components import StoreComponent


class TicketGrid(StoreComponent, DataTable):
    """Rows: id, status, title. Selecting a row emits TicketSelected.

    StoreComponent binding: bind_stores(dispatch=dispatch_store)
    Bound by DefaultLayout before compose; self-subscribes on mount and reads
    DispatchStoreSnapshot (duck-type compatible with DispatchSnapshot).
    """

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    class TicketSelected(Message):
        def __init__(self, ticket_id: str) -> None:
            self.ticket_id = ticket_id
            super().__init__()

    def __init__(self) -> None:
        DataTable.__init__(self, zebra_stripes=True, cursor_type="row")
        self._tickets: list[str] = []

    def on_mount(self) -> None:
        self.add_columns("id", "status", "title")
        super().on_mount()  # StoreComponent subscribes if bound

    def refresh_from_snapshot(self, snapshot: DispatchSnapshot) -> None:
        """Accepts both DispatchSnapshot (bridge) and DispatchStoreSnapshot (self-subscribe)."""
        self.clear()
        self._tickets = []
        for ticket in snapshot.tickets:
            self.add_row(
                ticket.id,
                ticket.status.value,
                ticket.title,
            )
            self._tickets.append(ticket.id)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._tickets):
            self.post_message(self.TicketSelected(self._tickets[idx]))
