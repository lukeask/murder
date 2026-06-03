"""Global quick-capture modal — recent entries + draft; ESC layering + chords."""

from __future__ import annotations

import asyncio
import datetime
import inspect
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Static, TextArea

LoadRecentFn = Callable[[], list[dict[str, Any]] | Awaitable[list[dict[str, Any]]]]
DismissPayload = tuple[bool, str]

RECENT_NOTE_ROWS = 12
_SHORT_CELL_VIS_MAX = 120
_SHORT_CELL_TAIL = 117


class RecentNotesTable(DataTable):
    """Recent-entry list — Enter returns focus to the draft."""

    def __init__(self) -> None:
        super().__init__(id="recent_table", cursor_type="row", zebra_stripes=True)

    def on_key(self, event: Key) -> None:
        if event.key == "enter":
            event.prevent_default()
            event.stop()
            screen = self.screen
            if isinstance(screen, NoteCaptureScreen):
                screen.set_focus(screen._draft_widget)
            return
        super().on_key(event)


class NoteCaptureScreen(ModalScreen[DismissPayload]):
    """Large modal for planning captures — matches ESC / chord behavior table."""

    BINDINGS = [
        Binding("escape", "screen_escape", priority=True),
        Binding("j", "relay_cursor_down", show=False),
        Binding("k", "relay_cursor_up", show=False),
        Binding("down", "relay_cursor_down", show=False),
        Binding("up", "relay_cursor_up", show=False),
    ]

    CSS = """
    NoteCaptureScreen {
        align: center middle;
    }
    #capture_shell {
        width: 94%;
        height: 90%;
        max-width: 98%;
        border: solid $primary;
        background: $surface;
        padding: 0 1;
    }
    #capture_header {
        dock: top;
        padding: 1 0;
        text-style: bold;
    }
    #capture_split {
        height: 1fr;
        min-height: 8;
    }
    #recent_table {
        width: 38%;
        border: solid $border;
        height: 1fr;
    }
    #preview_pane {
        width: 1fr;
        border: solid $border;
        border-left: none;
        height: 1fr;
        padding: 0 1;
        background: $boost;
    }
    #preview_static {
        height: auto;
    }
    #draft {
        dock: bottom;
        height: 30%;
        min-height: 5;
        max-height: 45%;
        border: solid $border;
        margin-top: 1;
    }
    #draft:focus {
        border: solid $accent;
    }
    """

    ESC_DOUBLE_TAP_S = 0.45
    BLUR_DELAY_S = 0.35

    class Draft(TextArea):
        """Draft region — Enter dismisses with submit intent; Shift+Enter newline; ESC upward."""

        def __init__(self, outer: NoteCaptureScreen) -> None:
            super().__init__(id="draft")
            self._outer = outer

        def on_key(self, event: Key) -> None:
            key = event.key
            outer = self._outer

            if key == "d" and outer.blur_timer_active():
                event.prevent_default()
                event.stop()
                outer.consume_escape_d_chord()
                return

            if key == "u" and outer.consume_undo_delete():
                event.prevent_default()
                event.stop()
                return

            if key == "escape":
                event.prevent_default()
                event.stop()
                outer.handle_escape_from_draft()
                return

            if key == "enter":
                event.prevent_default()
                event.stop()
                text = self.text.strip()
                if text:
                    outer._finish(submitted=True)
                return

            if key == "shift+enter":
                event.prevent_default()
                event.stop()
                self.insert("\n")
                return

            if key == "ctrl+v":
                event.prevent_default()
                event.stop()
                asyncio.create_task(self._paste_image())
                return

        async def _paste_image(self) -> None:
            from murder.tui.clipboard_image import has_clipboard_image, read_clipboard_image_png

            if not await has_clipboard_image():
                self.action_paste()
                return

            outer = self._outer
            outer._paste_counter += 1
            n = outer._paste_counter
            placeholder = f"[Image #{n} pasting…]"
            self.insert(placeholder)

            data = await read_clipboard_image_png()
            if data is None:
                replacement = "[Image paste failed]"
            else:
                images_dir = outer._images_dir
                images_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
                hex4 = secrets.token_hex(2)
                fname = f"note-img-{ts}-{hex4}.png"
                fpath = images_dir / fname
                fpath.write_bytes(data)
                replacement = f"![image]({fpath})"

            self.text = self.text.replace(placeholder, replacement, 1)

    def __init__(
        self,
        *,
        initial_draft: str,
        load_recent_rows: LoadRecentFn,
        images_dir: Path,
    ) -> None:
        super().__init__()
        self._initial_draft = initial_draft
        self._load_recent_rows = load_recent_rows
        self._images_dir = images_dir
        self._paste_counter = 0
        self._rows: list[dict[str, Any]] = []
        self._draft_esc_armed_at: float | None = None
        self._blur_after_idle: asyncio.TimerHandle | None = None
        self._draft_undo_snapshot: str | None = None
        self._draft_widget = NoteCaptureScreen.Draft(self)

    def blur_timer_active(self) -> bool:
        return self._blur_after_idle is not None

    def _cancel_blur_timer(self) -> None:
        if self._blur_after_idle is not None:
            self._blur_after_idle.cancel()
            self._blur_after_idle = None

    def _schedule_blur_to_table(self) -> None:
        self._cancel_blur_timer()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.BLUR_DELAY_S

        def _fire() -> None:
            self._blur_after_idle = None
            self._blur_draft_to_list()

        self._blur_after_idle = loop.call_at(deadline, _fire)

    def handle_escape_from_draft(self) -> None:
        now = time.monotonic()
        if (
            self._draft_esc_armed_at is not None
            and now - self._draft_esc_armed_at < self.ESC_DOUBLE_TAP_S
        ):
            self._cancel_blur_timer()
            self._draft_esc_armed_at = None
            self._finish(submitted=False)
            return
        self._draft_esc_armed_at = now
        self._schedule_blur_to_table()

    def consume_escape_d_chord(self) -> None:
        self._cancel_blur_timer()
        self._draft_esc_armed_at = None
        draft = self._draft_widget
        self._draft_undo_snapshot = draft.text
        draft.clear()

    def consume_undo_delete(self) -> bool:
        if self._draft_undo_snapshot is None:
            return False
        self._draft_widget.text = self._draft_undo_snapshot
        self._draft_undo_snapshot = None
        return True

    def _blur_draft_to_list(self) -> None:
        self._draft_esc_armed_at = None
        table = self.query_one("#recent_table", RecentNotesTable)
        self.set_focus(table)

    def compose(self) -> ComposeResult:
        with Vertical(id="capture_shell"):
            yield Static(
                "Quick capture — recent summaries · draft below · ESC from draft → list · "
                "ESC ESC closes · ESC then d clears draft · u undoes · Enter saves in background",
                id="capture_header",
            )
            with Horizontal(id="capture_split"):
                yield RecentNotesTable()
                with VerticalScroll(id="preview_pane"):
                    yield Static("", id="preview_static")
            yield self._draft_widget
            yield Footer()

    def on_mount(self) -> None:
        self._draft_widget.text = self._initial_draft
        table = self.query_one("#recent_table", RecentNotesTable)
        table.border_title = "recent (short)"
        preview = self.query_one("#preview_static", Static)
        preview.border_title = "cleaned"
        self.run_worker(self._hydrate_recent_rows(), exclusive=True, group="note_capture_load")
        self.set_focus(self._draft_widget)

    async def _hydrate_recent_rows(self) -> None:
        rows = self._load_recent_rows()
        if inspect.isawaitable(rows):
            rows = await rows
        table = self.query_one("#recent_table", RecentNotesTable)
        self._rows = list(rows)
        table.clear(columns=True)
        table.add_column("short_vers")
        for r in self._rows:
            sv = str(r.get("short_vers") or "").replace("\n", " ").strip()
            if len(sv) > _SHORT_CELL_VIS_MAX:
                sv = sv[:_SHORT_CELL_TAIL] + "..."
            table.add_row(sv)
        if self._rows:
            table.cursor_coordinate = (0, 0)
            self._sync_preview_row(0)

    def _sync_preview_row(self, row_index: int) -> None:
        preview = self.query_one("#preview_static", Static)
        if not self._rows or row_index < 0 or row_index >= len(self._rows):
            preview.update("")
            return
        cleaned = str(self._rows[row_index].get("cleaned") or "")
        preview.update(cleaned)

    @on(DataTable.RowHighlighted, "#recent_table")
    def _on_recent_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._sync_preview_row(event.cursor_row)

    def _focused_within_recent_table(self) -> bool:
        table = self.query_one("#recent_table", RecentNotesTable)
        cur = self.focused
        while cur is not None:
            if cur is table:
                return True
            cur = cur.parent  # type: ignore[assignment]
        return False

    def action_screen_escape(self) -> None:
        if self._focused_within_recent_table():
            self._finish(submitted=False)
            return
        self.handle_escape_from_draft()

    def action_relay_cursor_down(self) -> None:
        if self._focused_within_recent_table():
            self.query_one("#recent_table", RecentNotesTable).action_cursor_down()

    def action_relay_cursor_up(self) -> None:
        if self._focused_within_recent_table():
            self.query_one("#recent_table", RecentNotesTable).action_cursor_up()

    def _finish(self, *, submitted: bool) -> None:
        self._cancel_blur_timer()
        draft_snapshot = self._draft_widget.text
        self.dismiss((submitted, draft_snapshot))
