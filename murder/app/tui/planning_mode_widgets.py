"""Planning-mode widgets."""

from __future__ import annotations

from rich.markup import escape
from textual.binding import Binding
from textual.message import Message
from textual.widgets import DataTable, Markdown, RichLog

from murder.app.service.client_api import NotesSnapshot, PlansSnapshot, ReportsSnapshot
from murder.app.tui.live_log import LiveRichLog


class PlanList(DataTable):
    """DB-backed plan list."""

    BINDINGS = [
        Binding("enter", "open_selected", "Open", show=False),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("r", "deprecate_plan", "Deprecate"),
    ]

    class PlanHighlighted(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    class PlanOpened(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    class PlanDeprecateRequested(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    def __init__(self) -> None:
        super().__init__(zebra_stripes=True, cursor_type="row")
        self._plans: list[str] = []
        self._deprecate_armed_name: str | None = None
        self._last_rows: list[tuple[str, str, str, str]] = []
        self.border_title = ".murder/plans"
        # TODO(tui-planning): fold dynamic ticket ordering into this sidebar
        # once collaborator Planner/Notetaker personas can prioritize tickets.

    def on_mount(self) -> None:
        self.add_columns("name", "status", "rev", "sync")

    def refresh_from_snapshot(self, snapshot: PlansSnapshot) -> None:
        new_rows = [
            (p.name.removeprefix("plan-"), p.status, str(p.revision_count), p.sync_state)
            for p in snapshot.plans
        ]
        if new_rows == self._last_rows:
            return
        self._last_rows = new_rows
        row = self.cursor_row
        scroll_y = self.scroll_y
        with self.prevent(DataTable.RowHighlighted):
            self.clear()
            self._plans = []
            for plan in snapshot.plans:
                display_name = plan.name.removeprefix("plan-")
                self.add_row(
                    display_name,
                    plan.status,
                    str(plan.revision_count),
                    plan.sync_state,
                )
                self._plans.append(plan.name)
            if self._plans:
                self.move_cursor(row=min(max(row, 0), len(self._plans) - 1))
        self.scroll_y = scroll_y

    @property
    def selected_name(self) -> str | None:
        row = self.cursor_row
        if 0 <= row < len(self._plans):
            return self._plans[row]
        return None

    def select_name(self, name: str) -> None:
        if name in self._plans:
            self.move_cursor(row=self._plans.index(name))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._plans):
            name = self._plans[idx]
            if name != self._deprecate_armed_name:
                self.cancel_deprecate_confirmation()
            self.post_message(self.PlanHighlighted(name))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # DataTable emits RowSelected when clicking an already-highlighted row.
        # Mouse clicks should only highlight; Enter is the explicit open action.
        event.stop()

    def action_open_selected(self) -> None:
        name = self.selected_name
        if name:
            self.post_message(self.PlanOpened(name))

    def action_select_cursor(self) -> None:
        self.action_open_selected()

    def on_key(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._deprecate_armed_name is not None and event.key != "r":
            self.cancel_deprecate_confirmation()

    def action_deprecate_plan(self) -> None:
        name = self.selected_name
        if not name:
            return
        if self._deprecate_armed_name == name:
            self.cancel_deprecate_confirmation()
            self.post_message(self.PlanDeprecateRequested(name))
            return
        self._deprecate_armed_name = name
        self.border_subtitle = "press r again to deprecate"

    def cancel_deprecate_confirmation(self) -> None:
        self._deprecate_armed_name = None
        self.border_subtitle = ""


class NotesList(DataTable):
    """DB-backed list of active note documents."""

    BINDINGS = [
        Binding("enter", "open_selected", "Open", show=False),
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
        self._last_rows: list[tuple[str, str, str]] = []
        self.border_title = ".murder/notes"

    def on_mount(self) -> None:
        self.add_columns("note", "chars", "updated")

    def refresh_from_snapshot(self, snapshot: NotesSnapshot) -> None:
        new_rows = [
            (n.name, str(n.char_count), n.updated_at.isoformat()[:16])
            for n in snapshot.notes
        ]
        if new_rows == self._last_rows:
            return
        self._last_rows = new_rows
        row = self.cursor_row
        scroll_y = self.scroll_y
        with self.prevent(DataTable.RowHighlighted):
            self.clear()
            self._names = []
            for note in snapshot.notes:
                updated = note.updated_at.isoformat()[:16].replace("T", " ")
                self.add_row(note.name, str(note.char_count), updated)
                self._names.append(note.name)
            if self._names:
                self.move_cursor(row=min(max(row, 0), len(self._names) - 1))
        self.scroll_y = scroll_y

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
            name = self._names[idx]
            if name != self._retire_armed_name:
                self.cancel_retire_confirmation()
            self.post_message(self.NoteHighlighted(name))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # DataTable emits RowSelected when clicking an already-highlighted row.
        # Mouse clicks should only highlight; Enter is the explicit open action.
        event.stop()

    def action_open_selected(self) -> None:
        name = self.selected_name
        if name:
            self.post_message(self.NoteOpened(name))

    def action_select_cursor(self) -> None:
        self.action_open_selected()

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


class ReportsList(DataTable):
    """Filesystem-backed list of report documents."""

    BINDINGS = [
        Binding("enter", "open_selected", "Open", show=False),
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
    ]

    class ReportHighlighted(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    class ReportOpened(Message):
        def __init__(self, name: str) -> None:
            self.name = name
            super().__init__()

    def __init__(self) -> None:
        super().__init__(zebra_stripes=True, cursor_type="row")
        self._names: list[str] = []
        self._last_rows: list[tuple[str, str, str]] = []
        self.border_title = ".murder/reports"

    def on_mount(self) -> None:
        self.add_columns("report", "chars", "updated")

    def refresh_from_snapshot(self, snapshot: ReportsSnapshot) -> None:
        new_rows = [
            (r.name, str(r.char_count), r.updated_at.isoformat()[:16])
            for r in snapshot.reports
        ]
        if new_rows == self._last_rows:
            return
        self._last_rows = new_rows
        row = self.cursor_row
        scroll_y = self.scroll_y
        with self.prevent(DataTable.RowHighlighted):
            self.clear()
            self._names = []
            for report in snapshot.reports:
                updated = report.updated_at.isoformat()[:16].replace("T", " ")
                self.add_row(report.name, str(report.char_count), updated)
                self._names.append(report.name)
            if self._names:
                self.move_cursor(row=min(max(row, 0), len(self._names) - 1))
        self.scroll_y = scroll_y

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
            self.post_message(self.ReportHighlighted(self._names[idx]))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # DataTable emits RowSelected when clicking an already-highlighted row.
        # Mouse clicks should only highlight; Enter is the explicit open action.
        event.stop()

    def action_open_selected(self) -> None:
        name = self.selected_name
        if name:
            self.post_message(self.ReportOpened(name))

    def action_select_cursor(self) -> None:
        self.action_open_selected()


class PlanDocument(Markdown, can_focus=True):
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


class ReportDocument(Markdown, can_focus=True):
    """Live view of a report document (`.murder/reports/<name>.md`)."""

    BINDINGS = [
        ("j", "line_down", "Down"),
        ("k", "line_up", "Up"),
        ("down", "line_down", "Down"),
        ("up", "line_up", "Up"),
        ("pagedown", "page_down", "Page down"),
        ("pageup", "page_up", "Page up"),
    ]

    DEFAULT_CSS = """
    ReportDocument {
        border: solid $border;
        height: 1fr;
        width: 1fr;
        overflow-y: auto;
    }
    """

    _EMPTY = "# Reports\n\n_Add markdown under `.murder/reports`._"

    def __init__(self) -> None:
        super().__init__(self._EMPTY)
        self.can_focus = True
        self.border_title = "reports"
        self._last_render_key: tuple[str, str] | None = None

    async def show(self, name: str, body: str) -> None:
        """Render the report body; no-op when ``(name, normalized body)`` matches."""
        display = body.strip() or self._EMPTY
        if self._last_render_key == (name, display):
            return
        self._last_render_key = (name, display)
        self.border_title = f"reports · {name}"
        await self.update(display)

    def action_line_down(self) -> None:
        self.action_scroll_down()

    def action_line_up(self) -> None:
        self.action_scroll_up()


class ChatLog(LiveRichLog):
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
        overflow-x: hidden;
        overflow-y: auto;
    }
    """

    def __init__(self, *, agent_label: str = "agent") -> None:
        super().__init__(
            highlight=False,
            markup=True,
            min_width=1,
            wrap=True,
        )
        self._agent_label = agent_label
        self._last_render_key: tuple[str, tuple[tuple[str, str], ...], str | None] | None = None
        self.border_title = f"{agent_label} chat"

    def set_agent_label(self, agent_label: str) -> None:
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
        self.write(f"{self._tag(who)}: {escape(text)}")
        self.write("")

    def add_status(self, text: str) -> None:
        self.write(f"[dim]{escape(text)}[/]")

    def replace_transcript(
        self,
        turns: list[tuple[str, str]],
        *,
        status: str | None = None,
    ) -> None:
        """Replace transcript content in one rewrite pass."""
        render_key = (self._agent_label, tuple(turns), status)
        if render_key == self._last_render_key:
            return
        self._last_render_key = render_key

        def _write() -> None:
            for who, text in turns:
                self.add_turn(who, text)
            if status is not None:
                self.add_status(status)

        self.replace_lines(_write)

    def set_turns(self, turns: list[tuple[str, str]]) -> None:
        """Replace the whole transcript (the parsed log can change wholesale)."""
        self.replace_transcript(turns)
