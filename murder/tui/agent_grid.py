"""Agent grid widgets for the crows view."""

from __future__ import annotations

import sqlite3

from textual.message import Message
from textual.widgets import DataTable


class AgentGrid(DataTable):
    """DB-backed agent/session list.

    TODO(tui-crows): replace the table with a responsive 3x3 tile layout once
    Textual layout details are settled. The selection and tmux mirror behavior
    are the important first slice.
    """

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    class AgentHighlighted(Message):
        def __init__(self, agent_id: str, session: str | None) -> None:
            self.agent_id = agent_id
            self.session = session
            super().__init__()

    class AgentOpened(Message):
        def __init__(self, agent_id: str, session: str | None) -> None:
            self.agent_id = agent_id
            self.session = session
            super().__init__()

    def __init__(self) -> None:
        super().__init__(zebra_stripes=True, cursor_type="row")
        self._agents: list[tuple[str, str | None]] = []
        self.border_title = "crows"

    def on_mount(self) -> None:
        self.add_columns(
            "agent", "role", "ticket", "agent status", "ticket status", "harness", "session"
        )

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        row = self.cursor_row
        rows = db.execute(
            """
            SELECT a.agent_id, a.role, a.ticket_id, a.status, a.session,
                   COALESCE(t.status, '') AS ticket_status,
                   COALESCE(t.harness, '') AS harness
              FROM agents a
              LEFT JOIN tickets t ON t.id = a.ticket_id
             ORDER BY
                   CASE a.status
                     WHEN 'running' THEN 0
                     WHEN 'escalating' THEN 1
                     WHEN 'blocked' THEN 2
                     WHEN 'idle' THEN 3
                     WHEN 'done' THEN 4
                     ELSE 5
                   END,
                   a.started_at DESC,
                   a.agent_id
            """
        ).fetchall()
        self.clear()
        self._agents = []
        for r in rows:
            session = r["session"]
            self.add_row(
                r["agent_id"],
                r["role"],
                r["ticket_id"] or "-",
                r["status"],
                r["ticket_status"] or "-",
                r["harness"] or "-",
                session or "-",
            )
            self._agents.append((r["agent_id"], session))
        if self._agents:
            self.move_cursor(row=min(max(row, 0), len(self._agents) - 1))

    @property
    def selected_agent(self) -> tuple[str, str | None] | None:
        row = self.cursor_row
        if 0 <= row < len(self._agents):
            return self._agents[row]
        return None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._agents):
            agent_id, session = self._agents[idx]
            self.post_message(self.AgentHighlighted(agent_id, session))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._agents):
            agent_id, session = self._agents[idx]
            self.post_message(self.AgentOpened(agent_id, session))
