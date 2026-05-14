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
        self.border_title = ".murder/plans"
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


class NotesList(DataTable):
    """DB-backed list of dated notes documents (`.murder/notes/<date>.md`).

    The notetaker owns the note bodies; this is just a sidebar "filetree" so
    you can see which days have notes and how big they are. Highlighting a row
    surfaces it; the notetaker view keeps showing the live (today's) note.
    """

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    class NoteHighlighted(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    class NoteOpened(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    def __init__(self) -> None:
        super().__init__(zebra_stripes=True, cursor_type="row")
        self._names: list[str] = []
        self.border_title = ".murder/notes"

    def on_mount(self) -> None:
        self.add_columns("date", "chars", "updated")

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        row = self.cursor_row
        rows = db.execute(
            """
            SELECT name, length(body) AS size, updated_at
              FROM notes
             ORDER BY name DESC
            """
        ).fetchall()
        self.clear()
        self._names = []
        for r in rows:
            updated = str(r["updated_at"])[:16].replace("T", " ")
            self.add_row(r["name"], str(r["size"]), updated)
            self._names.append(r["name"])
        if self._names:
            self.move_cursor(row=min(max(row, 0), len(self._names) - 1))

    @property
    def selected_name(self) -> str | None:
        row = self.cursor_row
        if 0 <= row < len(self._names):
            return self._names[row]
        return None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._names):
            self.post_message(self.NoteHighlighted(self._names[idx]))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._names):
            self.post_message(self.NoteOpened(self._names[idx]))


class PlanDocument(Markdown):
    BINDINGS = [
        ("j", "line_down", "Down"),
        ("k", "line_up", "Up"),
        ("down", "line_down", "Down"),
        ("up", "line_up", "Up"),
        ("pagedown", "page_down", "Page down"),
        ("pageup", "page_up", "Page up"),
    ]

    DEFAULT_CSS = """
    PlanDocument {
        border: solid $border;
        height: 1fr;
        width: 1fr;
        overflow-y: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__(
            "No plan selected.\n\nUse the collaborator chat to shape a plan, "
            "or add markdown under `.murder/plans`."
        )
        self.border_title = "(no plan selected)"

    def action_line_down(self) -> None:
        self.action_scroll_down()

    def action_line_up(self) -> None:
        self.action_scroll_up()


class NotesDocument(Markdown, can_focus=True):
    """Live view of the notetaker's notes document (`.murder/notes/<date>.md`)."""

    BINDINGS = [
        ("j", "line_down", "Down"),
        ("k", "line_up", "Up"),
        ("down", "line_down", "Down"),
        ("up", "line_up", "Up"),
        ("pagedown", "page_down", "Page down"),
        ("pageup", "page_up", "Page up"),
    ]

    DEFAULT_CSS = """
    NotesDocument {
        border: solid $border;
        height: 1fr;
        width: 1fr;
        overflow-y: auto;
    }
    """

    _EMPTY = (
        "# Notes\n\n_No notes yet — type in the chat box and the notetaker will "
        "tidy them into this document._"
    )

    def __init__(self) -> None:
        super().__init__(self._EMPTY)
        self.can_focus = True
        self.border_title = "notes"

    async def show(self, name: str, body: str) -> None:
        self.border_title = f"notes · {name}"
        await self.update(body.strip() or self._EMPTY)

    def action_line_down(self) -> None:
        self.action_scroll_down()

    def action_line_up(self) -> None:
        self.action_scroll_up()


class ChatLog(RichLog):
    """Append-only chat transcript widget, reused for any agent chat (notetaker,
    collaborator). ``"you"``/``"user"`` is the human; ``"agent"``/``"assistant"``
    and the configured ``agent_label`` all render as that agent's name.
    """

    BINDINGS = [
        ("j", "line_down", "Down"),
        ("k", "line_up", "Up"),
        ("down", "line_down", "Down"),
        ("up", "line_up", "Up"),
    ]

    DEFAULT_CSS = """
    ChatLog {
        border: solid $border;
        height: 1fr;
        width: 1fr;
    }
    """

    def __init__(self, *, agent_label: str = "agent") -> None:
        super().__init__(highlight=False, markup=True, wrap=True, auto_scroll=True)
        self._agent_label = agent_label
        self.border_title = f"{agent_label} chat"

    def action_line_down(self) -> None:
        self.action_scroll_down()

    def action_line_up(self) -> None:
        self.action_scroll_up()

    def _tag(self, who: str) -> str:
        if who in ("you", "user"):
            return "[b cyan]you[/]"
        if who in ("agent", "assistant", self._agent_label):
            return f"[b green]{escape(self._agent_label)}[/]"
        return f"[b]{escape(who)}[/]"

    def add_turn(self, who: str, text: str) -> None:
        self.write(f"{self._tag(who)}  {escape(text)}")
        self.write("")

    def add_status(self, text: str) -> None:
        self.write(f"[dim]{escape(text)}[/]")

    def set_turns(self, turns: list[tuple[str, str]]) -> None:
        """Replace the whole transcript (the parsed log can change wholesale)."""
        self.clear()
        for who, text in turns:
            self.add_turn(who, text)
