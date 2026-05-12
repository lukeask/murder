"""Planning-mode widgets."""

from __future__ import annotations

import sqlite3

from rich.markup import escape
from textual.message import Message
from textual.widgets import DataTable, Markdown, RichLog


class PlanList(DataTable):
    """DB-backed plan list."""

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

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
        self.border_title = ".agents/plans"
        # TODO(tui-planning): fold dynamic ticket ordering into this sidebar
        # once collaborator Planner/Notetaker personas can prioritize tickets.

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
        border: solid $border;
        height: 1fr;
        width: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__(
            "No plan selected.\n\nUse the collaborator chat to shape a plan, "
            "or add markdown under `.agents/plans`."
        )
        self.border_title = "(no plan selected)"


class NotesDocument(Markdown):
    """Live view of the notetaker's notes document (`.agents/notes/<date>.md`)."""

    DEFAULT_CSS = """
    NotesDocument {
        border: solid $border;
        height: 1fr;
        width: 1fr;
    }
    """

    _EMPTY = (
        "# Notes\n\n_No notes yet — type in the chat box and the notetaker will "
        "tidy them into this document._"
    )

    def __init__(self) -> None:
        super().__init__(self._EMPTY)
        self.border_title = "notes"

    async def show(self, name: str, body: str) -> None:
        self.border_title = f"notes · {name}"
        await self.update(body.strip() or self._EMPTY)


class NotetakerChat(RichLog):
    """Append-only chat transcript with the notetaker."""

    DEFAULT_CSS = """
    NotetakerChat {
        border: solid $border;
        height: 1fr;
        width: 36%;
    }
    """

    _TAGS = {"you": "[b cyan]you[/]", "notetaker": "[b green]notetaker[/]"}

    def __init__(self) -> None:
        super().__init__(highlight=False, markup=True, wrap=True, auto_scroll=True)
        self.border_title = "notetaker chat"

    def add_turn(self, who: str, text: str) -> None:
        tag = self._TAGS.get(who, f"[b]{escape(who)}[/]")
        self.write(f"{tag}  {escape(text)}")
        self.write("")

    def add_status(self, text: str) -> None:
        self.write(f"[dim]{escape(text)}[/]")
