"""Planning-mode widgets."""

from __future__ import annotations

from rich.markup import escape
from textual.message import Message
from textual.widgets import DataTable, Markdown, RichLog

from murder.service.client_api import NotesSnapshot, PlansSnapshot


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

    def refresh_from_snapshot(self, snapshot: PlansSnapshot) -> None:
        row = self.cursor_row
        self.clear()
        self._plans = []
        for plan in snapshot.plans:
            self.add_row(
                plan.name,
                plan.status,
                str(plan.revision_count),
                plan.sync_state,
            )
            self._plans.append(plan.name)
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
    """DB-backed list of active note documents."""

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("r", "retire_note", "Retire"),
    ]

    class NoteHighlighted(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    class NoteOpened(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    class NoteRetireRequested(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    def __init__(self) -> None:
        super().__init__(zebra_stripes=True, cursor_type="row")
        self._names: list[str] = []
        self._retire_armed_name: str | None = None
        self.border_title = ".murder/notes"

    def on_mount(self) -> None:
        self.add_columns("note", "chars", "updated")

    def refresh_from_snapshot(self, snapshot: NotesSnapshot) -> None:
        row = self.cursor_row
        self.clear()
        self._names = []
        for note in snapshot.notes:
            updated = note.updated_at.isoformat()[:16].replace("T", " ")
            self.add_row(note.name, str(note.char_count), updated)
            self._names.append(note.name)
        if self._names:
            self.move_cursor(row=min(max(row, 0), len(self._names) - 1))

    @property
    def selected_name(self) -> str | None:
        row = self.cursor_row
        if 0 <= row < len(self._names):
            return self._names[row]
        return None

    def select_name(self, name: str) -> None:
        if name in self._names:
            self.move_cursor(row=self._names.index(name))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._names):
            if self._retire_armed_name != self._names[idx]:
                self.cancel_retire_confirmation()
            self.post_message(self.NoteHighlighted(self._names[idx]))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._names):
            self.post_message(self.NoteOpened(self._names[idx]))

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._retire_armed_name is not None and event.key != "r":
            self.cancel_retire_confirmation()

    def action_retire_note(self) -> None:
        name = self.selected_name
        if not name:
            return
        if self._retire_armed_name == name:
            self.cancel_retire_confirmation()
            self.post_message(self.NoteRetireRequested(name))
            return
        self._retire_armed_name = name
        self.border_subtitle = "press r again to retire"

    def cancel_retire_confirmation(self) -> None:
        self._retire_armed_name = None
        self.border_subtitle = ""


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
        self._last_render_key: tuple[str, str] | None = None

    async def set_plan_markdown(self, title: str, markdown: str) -> None:
        """Update the viewer only when title or body changed — skips Rich re-parse."""
        if self._last_render_key == (title, markdown):
            return
        self._last_render_key = (title, markdown)
        self.border_title = title
        await self.update(markdown)

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
        "# Notes\n\n_Use **ctrl+n** (or `/note` in chat) for quick capture; entries "
        "land in the database from the overlay pipeline._"
    )

    def __init__(self) -> None:
        super().__init__(self._EMPTY)
        self.can_focus = True
        self.border_title = "notes"
        self._last_render_key: tuple[str, str] | None = None

    async def show(self, name: str, body: str) -> None:
        """Render the note body; no-op when ``(name, normalized body)`` matches last tick."""
        display = body.strip() or self._EMPTY
        if self._last_render_key == (name, display):
            return
        self._last_render_key = (name, display)
        self.border_title = f"notes · {name}"
        await self.update(display)

    def action_line_down(self) -> None:
        self.action_scroll_down()

    def action_line_up(self) -> None:
        self.action_scroll_up()


class ChatLog(RichLog):
    """Append-only chat transcript widget, reused for agent chats.

    ``"you"``/``"user"`` is the human; ``"agent"``/``"assistant"`` and the
    configured ``agent_label`` all render as that agent's name.
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
