"""Crows place — tail-wall of in-flight crow tiles, enlarge-to-mirror.

VISION.md §3.1 / §3.3 / JC-5: the Crows place is a grid of small-multiple
tiles, one per in-flight crow, each showing the last N lines of that
crow's tmux pane, a ticket-id + title header, and a border colored by
client-side health. Selecting a tile enlarges it into a full pane mirror
in place; ESC or `q` returns to the wall.

Tiles render from :class:`~murder.service.client_api.CrowSnapshot`
fetched over the service bus. Pane tails use :meth:`~murder.tui.client.TuiRuntimeClient.capture_pane`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Grid, ScrollableContainer
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import RichLog, Static

from murder.service.client_api import CrowSessionSummary, CrowSnapshot
from murder.tui.crow_health import Health, classify, is_stuck
from murder.tui.pane_capture import CapturePaneFn, PaneCaptureError
from murder.tui.pane_mirror import PaneMirror
from murder.tui.perf_log import PerfLog

TILE_LINES_RAW = 40
"""Raw-mode tail: last N lines of pane; large enough to show the input box and permission prompts."""

TILE_LINES_PARSED = 200
"""Parsed-mode capture: enough history for a meaningful transcript scroll."""

CAPTURE_TIMEOUT_S = 2.0
"""Per-tile tmux capture timeout — a stuck pane must not block the wall."""

GRID_TARGET_COLS = 3
"""Tail-wall packs roughly into this many columns; rows scale by count."""

TERMINAL_AGENT_STATUSES = frozenset({"done", "dead"})
"""Agent states excluded from the wall."""

TERMINAL_TICKET_STATUSES = frozenset({"done", "failed"})
"""Ticket states that indicate the work item is closed."""

FAILED_STALE_AFTER = timedelta(hours=2)
"""Hide failed agents after this long without a recent heartbeat."""

_STATUS_SORT_RANK = {
    "escalating": 0,
    "blocked": 1,
    "running": 2,
    "idle": 3,
    "failed": 4,
}

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CrowEntry:
    """One tile in the wall, projected from :class:`CrowSessionSummary`."""

    agent_id: str
    ticket_id: str
    ticket_title: str | None
    harness: str
    status: str
    session: str | None
    health: Health
    started_at: datetime | None = None


class CrowRosterRow(Widget):
    """Two-line roster entry for one crow."""

    DEFAULT_CSS = """
    CrowRosterRow {
        height: 2;
        padding: 0 1;
        border-left: tall $border;
        background: $surface;
    }
    CrowRosterRow:focus {
        background: $primary 15%;
        border-left: tall $primary;
    }
    CrowRosterRow.-health-red    { border-left: tall $error; }
    CrowRosterRow.-health-yellow { border-left: tall $warning; }
    CrowRosterRow.-health-green  { border-left: tall $success; }
    CrowRosterRow.-health-neutral{ border-left: tall $border; }
    CrowRosterRow.-kill-pending  { background: $error 10%; }
    """

    can_focus = True

    def __init__(
        self,
        entry: CrowEntry,
        *,
        favorite: bool = False,
        pane_visible: bool = False,
        kill_pending: bool = False,
    ) -> None:
        super().__init__()
        self._entry = entry
        self._favorite = favorite
        self._pane_visible = pane_visible
        self._kill_pending = kill_pending
        self._line1 = Static("", markup=False)
        self._line2 = Static("", markup=False)

    def compose(self) -> ComposeResult:
        yield self._line1
        yield self._line2

    def on_mount(self) -> None:
        self._refresh_content()
        self._refresh_classes()

    def update(
        self,
        entry: CrowEntry,
        *,
        favorite: bool,
        pane_visible: bool,
        kill_pending: bool,
    ) -> None:
        changed = (
            entry != self._entry
            or favorite != self._favorite
            or pane_visible != self._pane_visible
            or kill_pending != self._kill_pending
        )
        self._entry = entry
        self._favorite = favorite
        self._pane_visible = pane_visible
        self._kill_pending = kill_pending
        if changed:
            self._refresh_content()
            self._refresh_classes()

    def _refresh_content(self) -> None:
        e = self._entry
        star = "★ " if self._favorite else "  "
        eye = "[pane]" if self._pane_visible else ""
        ticket = f"[{e.ticket_id}]" if e.ticket_id else ""
        name = e.session or e.agent_id
        status_chip = e.status.upper()
        line1_parts = [star + name, status_chip]
        if ticket:
            line1_parts.append(ticket)
        if eye:
            line1_parts.append(eye)
        self._line1.update("  ".join(line1_parts))

        if self._kill_pending:
            self._line2.update("  murder this crow? [m / ctrl+m = confirm  ·  any other key = cancel]")
        else:
            self._line2.update("  doing: ")

    def _refresh_classes(self) -> None:
        for h in Health:
            self.remove_class(f"-health-{h.value}")
        self.add_class(f"-health-{self._entry.health.value}")
        self.set_class(self._kill_pending, "-kill-pending")

    @property
    def agent_id(self) -> str:
        return self._entry.agent_id

    @property
    def entry(self) -> CrowEntry:
        return self._entry


class CrowRosterList(ScrollableContainer):
    """Scrollable, keyboard-driven roster of active crows."""

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("f", "toggle_favorite", "Favorite", show=False),
        Binding("enter", "toggle_pane", "Toggle pane", show=False),
        Binding("ctrl+m", "kill_confirm", "Kill", show=False),
        Binding("m", "kill_confirm_m", "Kill confirm", show=False),
    ]

    class CrowSelected(Message):
        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id
            super().__init__()

    class PaneVisibilityChanged(Message):
        def __init__(self, visible: set[str]) -> None:
            self.visible = frozenset(visible)
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self._favorites: set[str] = set()
        self._pane_visible: set[str] = set()
        self._kill_pending: str | None = None
        self._rows: dict[str, CrowRosterRow] = {}
        self._order: list[str] = []
        self._prefs_path: Path | None = None
        self._last_entries: list[CrowEntry] = []

    def set_prefs_path(self, path: Path) -> None:
        self._prefs_path = path
        self._load_favorites()

    def _load_favorites(self) -> None:
        if self._prefs_path is None or not self._prefs_path.exists():
            return
        try:
            data = json.loads(self._prefs_path.read_text())
            favorites = data.get("favorites", [])
            if isinstance(favorites, list):
                self._favorites = {str(agent_id) for agent_id in favorites}
        except Exception:
            logger.debug("failed to load TUI favorites from %s", self._prefs_path, exc_info=True)

    def _save_favorites(self) -> None:
        if self._prefs_path is None:
            return
        try:
            self._prefs_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._prefs_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"favorites": sorted(self._favorites)}))
            tmp.replace(self._prefs_path)
        except Exception:
            logger.debug("failed to save TUI favorites to %s", self._prefs_path, exc_info=True)

    def reconcile(self, entries: list[CrowEntry]) -> None:
        self._last_entries = list(entries)
        entries_by_id = {entry.agent_id: entry for entry in entries}
        ordered_entries = sorted(entries, key=self._sort_entry)
        ordered_ids = [entry.agent_id for entry in ordered_entries]

        for agent_id in list(self._rows):
            if agent_id not in entries_by_id:
                row = self._rows.pop(agent_id)
                row.remove()
                if self._kill_pending == agent_id:
                    self._kill_pending = None
                self._pane_visible.discard(agent_id)

        for entry in ordered_entries:
            row = self._rows.get(entry.agent_id)
            if row is None:
                row = CrowRosterRow(
                    entry,
                    favorite=entry.agent_id in self._favorites,
                    pane_visible=entry.agent_id in self._pane_visible,
                    kill_pending=self._kill_pending == entry.agent_id,
                )
                self._rows[entry.agent_id] = row
                self.mount(row)
            else:
                self._update_row(row, entry)

        if ordered_ids != self._order:
            for index, agent_id in enumerate(ordered_ids):
                row = self._rows.get(agent_id)
                if row is not None and row.is_mounted:
                    self.move_child(row, before=index)
        self._order = ordered_ids

    def on_key(self, event: events.Key) -> None:
        if self._kill_pending is None:
            return
        if event.key not in {"ctrl+m", "m"}:
            self._clear_kill_pending()
            event.prevent_default()
            event.stop()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        del parameters
        if self._kill_pending is not None and action not in {
            "kill_confirm",
            "kill_confirm_m",
        }:
            self._clear_kill_pending()
            return False
        return True

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if isinstance(event.widget, CrowRosterRow):
            self.post_message(self.CrowSelected(event.widget.agent_id))

    def action_cursor_down(self) -> None:
        self._move_focus(1)

    def action_cursor_up(self) -> None:
        self._move_focus(-1)

    def action_toggle_favorite(self) -> None:
        row = self._focused_row()
        if row is None:
            return
        agent_id = row.agent_id
        if agent_id in self._favorites:
            self._favorites.remove(agent_id)
        else:
            self._favorites.add(agent_id)
        self._save_favorites()
        self.reconcile(self._last_entries)
        focused = self._rows.get(agent_id)
        if focused is not None:
            focused.focus()

    def action_toggle_pane(self) -> None:
        row = self._focused_row()
        if row is None:
            return
        agent_id = row.agent_id
        if agent_id in self._pane_visible:
            self._pane_visible.remove(agent_id)
        else:
            self._pane_visible.add(agent_id)
        self._update_row(row, row.entry)
        self.post_message(self.PaneVisibilityChanged(self._pane_visible))

    def add_rogue(self, agent_id: str) -> None:
        self._favorites.add(agent_id)
        self._pane_visible.add(agent_id)
        self._save_favorites()
        row = self._rows.get(agent_id)
        if row is not None:
            self._update_row(row, row.entry)
        self.post_message(self.PaneVisibilityChanged(self._pane_visible))

    def hide_agent(self, agent_id: str) -> bool:
        if agent_id not in self._pane_visible:
            return False
        self._pane_visible.remove(agent_id)
        row = self._rows.get(agent_id)
        if row is not None:
            self._update_row(row, row.entry)
        self.post_message(self.PaneVisibilityChanged(self._pane_visible))
        return True

    @property
    def pane_visible(self) -> frozenset[str]:
        return frozenset(self._pane_visible)

    def focus_agent(self, agent_id: str) -> bool:
        row = self._rows.get(agent_id)
        if row is None:
            return False
        row.focus()
        return True

    def focus_first_row(self) -> bool:
        if not self._order:
            return False
        return self.focus_agent(self._order[0])

    def action_kill_confirm(self) -> None:
        self._handle_kill_confirm()

    def action_kill_confirm_m(self) -> None:
        row = self._focused_row()
        if row is None:
            self._clear_kill_pending()
            return
        if self._kill_pending != row.agent_id:
            self._clear_kill_pending()
            return
        self._handle_kill_confirm()

    def _handle_kill_confirm(self) -> None:
        row = self._focused_row()
        if row is None:
            return
        agent_id = row.agent_id
        if self._kill_pending == agent_id:
            logger.info("TODO: dispatch agent.stop for %s", agent_id)
            self._clear_kill_pending()
            return
        self._clear_kill_pending()
        self._kill_pending = agent_id
        self._update_row(row, row.entry)

    def _clear_kill_pending(self) -> None:
        if self._kill_pending is None:
            return
        agent_id = self._kill_pending
        self._kill_pending = None
        row = self._rows.get(agent_id)
        if row is not None:
            self._update_row(row, row.entry)

    def _focused_row(self) -> CrowRosterRow | None:
        focused = self.app.focused
        if isinstance(focused, CrowRosterRow) and focused.agent_id in self._rows:
            return focused
        return None

    def _move_focus(self, delta: int) -> None:
        if not self._order:
            return
        row = self._focused_row()
        if row is None:
            idx = 0 if delta > 0 else len(self._order) - 1
        else:
            idx = max(0, min(len(self._order) - 1, self._order.index(row.agent_id) + delta))
        next_row = self._rows.get(self._order[idx])
        if next_row is not None:
            next_row.focus()

    def _sort_entry(self, entry: CrowEntry) -> tuple[bool, float, str]:
        started = entry.started_at
        return (
            entry.agent_id not in self._favorites,
            -(started.timestamp() if started else 0),
            entry.agent_id,
        )

    def _update_row(self, row: CrowRosterRow, entry: CrowEntry) -> None:
        row.update(
            entry,
            favorite=entry.agent_id in self._favorites,
            pane_visible=entry.agent_id in self._pane_visible,
            kill_pending=self._kill_pending == entry.agent_id,
        )


def entries_from_snapshot(
    snapshot: CrowSnapshot,
    *,
    now: datetime | None = None,
) -> list[CrowEntry]:
    """Project snapshot sessions into wall entries, filtered and sorted."""
    now = now or datetime.now(timezone.utc)
    entries: list[CrowEntry] = []
    for session in snapshot.sessions:
        entry = _entry_from_session(session, now=now)
        if entry is not None:
            entries.append(entry)
    entries.sort(
        key=lambda e: (
            _STATUS_SORT_RANK.get(e.status, 99),
            e.ticket_id or "",
            e.agent_id,
        )
    )
    return entries


def _entry_from_session(
    session: CrowSessionSummary,
    *,
    now: datetime,
) -> CrowEntry | None:
    if session.role not in {"crow", "rogue"}:
        return None
    status = session.status
    if status in TERMINAL_AGENT_STATUSES:
        return None
    if status == "failed" and not _keep_failed_session(session, now=now):
        return None
    tile_id = session.agent_id or session.session_name or session.ticket_id or ""
    if not tile_id:
        return None
    title = session.ticket_title or session.harness or session.ticket_id or tile_id
    return CrowEntry(
        agent_id=tile_id,
        ticket_id=session.ticket_id or "",
        ticket_title=title,
        harness=session.harness or "",
        status=status,
        session=session.session_name,
        health=_health_for_summary(session, now=now),
        started_at=session.started_at,
    )


def _health_for_summary(session: CrowSessionSummary, *, now: datetime) -> Health:
    return classify(
        status=session.status,
        open_escalations=session.open_escalations,
        max_severity=session.max_severity,
        stuck=is_stuck(status=session.status, last_seen=session.last_seen, now=now),
    )


def _keep_failed_session(session: CrowSessionSummary, *, now: datetime) -> bool:
    if session.status != "failed":
        return True
    ticket_status = session.ticket_status or ""
    if ticket_status and ticket_status not in TERMINAL_TICKET_STATUSES:
        return True
    last_seen = session.last_seen or session.started_at
    if last_seen is None:
        return True
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    else:
        last_seen = last_seen.astimezone(timezone.utc)
    return now - last_seen <= FAILED_STALE_AFTER


def _parse_tile_text(pane_text: str, harness_kind: str) -> list[tuple[str, str]]:
    """Parse a pane capture into (role, text) turns for the given harness kind."""
    from murder.harnesses import REGISTRY
    from murder.harnesses.transcripts import parser_for_harness_kind

    cls = REGISTRY.get(harness_kind or "")
    if cls is None:
        return []
    parser = parser_for_harness_kind(
        harness_kind,
        prompt_markers=getattr(cls, "transcript_prompt_markers", ()),
        drop_substrings=getattr(cls, "transcript_drop_substrings", ()),
    )
    if parser is None:
        return []
    return parser.parse(pane_text)


class CrowTile(Container):
    """One tile in the wall: header + raw tail OR parsed chat transcript."""

    DEFAULT_CSS = """
    CrowTile {
        border: solid $border;
        padding: 0 1;
        height: 1fr;
        width: 1fr;
        layout: vertical;
    }
    CrowTile.-health-red    { border: solid $error; }
    CrowTile.-health-yellow { border: solid $warning; }
    CrowTile.-health-green  { border: solid $success; }
    CrowTile.-health-neutral{ border: solid $border; }
    CrowTile:focus,
    CrowTile:focus-within { border: heavy $accent; }
    CrowTile > RichLog {
        height: 1fr;
        width: 1fr;
        background: transparent;
    }
    CrowTile > ChatLog {
        height: 1fr;
        width: 1fr;
        border: none;
        padding: 0;
    }
    """

    can_focus = True
    BINDINGS = [
        Binding("ctrl+o", "open", "Enlarge", show=False),
        Binding("ctrl+y", "toggle_view", "Parsed/Raw", show=False),
    ]

    class Highlighted(Message):
        def __init__(self, entry: CrowEntry) -> None:
            self.entry = entry
            super().__init__()

    class Opened(Message):
        def __init__(self, entry: CrowEntry) -> None:
            self.entry = entry
            super().__init__()

    class ViewToggled(Message):
        def __init__(self, entry: CrowEntry, raw_mode: bool) -> None:
            self.entry = entry
            self.raw_mode = raw_mode
            super().__init__()

    def __init__(self, entry: CrowEntry) -> None:
        super().__init__()
        self._entry = entry
        self._raw_mode = True
        self._last_turns: list[tuple[str, str]] = []
        self._raw_log = RichLog(highlight=False, markup=False, wrap=True, auto_scroll=True)
        # Import here to avoid requiring planning_mode_widgets at module load time.
        from murder.tui.planning_mode_widgets import ChatLog as _ChatLog

        self._chat_log = _ChatLog(agent_label=entry.harness or "agent")

    @property
    def entry(self) -> CrowEntry:
        return self._entry

    @property
    def raw_mode(self) -> bool:
        return self._raw_mode

    def compose(self) -> ComposeResult:
        yield self._raw_log
        yield self._chat_log

    def on_mount(self) -> None:
        self._apply_entry()
        self._chat_log.display = False

    def on_key(self, event: events.Key) -> None:
        if self._raw_mode:
            return
        if event.key in ("j", "down"):
            self._chat_log.action_scroll_down()
            event.stop()
        elif event.key in ("k", "up"):
            self._chat_log.action_scroll_up()
            event.stop()

    def update_entry(self, entry: CrowEntry) -> None:
        """Reconcile after a snapshot refresh; rebuild border + header in place."""
        self._entry = entry
        self._apply_entry()

    def set_tail(self, text: str) -> None:
        """Update the raw log view (called on each refresh tick)."""
        self._raw_log.clear()
        for line in text.splitlines():
            self._raw_log.write(line)

    def set_parsed(self, turns: list[tuple[str, str]], harness_kind: str = "") -> None:
        """Update the parsed chat log; skips if turns are unchanged."""
        if turns == self._last_turns:
            return
        self._last_turns = turns
        self._chat_log.set_turns(turns)
        if not turns:
            kind = harness_kind or self._entry.harness or "unknown"
            self._chat_log.add_status(f"(no transcript parser for '{kind}')")

    def action_toggle_view(self) -> None:
        self._raw_mode = not self._raw_mode
        self._raw_log.display = self._raw_mode
        self._chat_log.display = not self._raw_mode
        self.post_message(self.ViewToggled(self._entry, self._raw_mode))

    def _apply_entry(self) -> None:
        e = self._entry
        ticket = e.ticket_id or "—"
        title = e.ticket_title or e.harness or "crow"
        self.border_title = f"{ticket} · {title}"
        self.border_subtitle = e.session or "(no session)"
        for h in Health:
            self.remove_class(f"-health-{h.value}")
        self.add_class(f"-health-{e.health.value}")

    def on_focus(self) -> None:
        self.post_message(self.Highlighted(self._entry))

    def action_open(self) -> None:
        self.post_message(self.Opened(self._entry))

    async def on_click(self) -> None:  # type: ignore[override]
        self.focus()


class _EmptyMessage(Static):
    DEFAULT_CSS = """
    _EmptyMessage {
        content-align: center middle;
        height: 1fr;
        width: 1fr;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__("(press Enter on an agent to show its pane)")


class TailWall(Grid):
    """Grid of CrowTiles. Owns reconciliation against snapshot entries."""

    DEFAULT_CSS = """
    TailWall {
        grid-gutter: 0;
        height: 1fr;
        width: 1fr;
    }
    """

    BINDINGS = [
        Binding("h", "move_left", "Left", show=False),
        Binding("l", "move_right", "Right", show=False),
        Binding("j", "move_down", "Down", show=False),
        Binding("k", "move_up", "Up", show=False),
        Binding("left", "move_left", "Left", show=False),
        Binding("right", "move_right", "Right", show=False),
        Binding("down", "move_down", "Down", show=False),
        Binding("up", "move_up", "Up", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._tiles: dict[str, CrowTile] = {}
        self._order: list[str] = []
        self._empty: _EmptyMessage | None = None
        self._cols: int = 1

    def reconcile(self, entries: list[CrowEntry]) -> tuple[list[str], int, int, int]:
        """Make the visible tile set match ``entries``.

        Returns ``(order, n_mounted, n_removed, n_updated)``.
        """
        new_ids = [e.agent_id for e in entries]
        removed = 0
        mounted = 0
        updated = 0
        if entries and self._empty is not None:
            self._empty.remove()
            self._empty = None
        if not entries:
            for agent_id in list(self._tiles):
                self._tiles.pop(agent_id).remove()
                removed += 1
            self._order = []
            if self._empty is None:
                self._empty = _EmptyMessage()
                self.mount(self._empty)
                self.styles.grid_size_columns = 1
                self.styles.grid_size_rows = 1
            return [], 0, removed, 0
        for agent_id in list(self._tiles):
            if agent_id not in new_ids:
                self._tiles.pop(agent_id).remove()
                removed += 1
        for entry in entries:
            tile = self._tiles.get(entry.agent_id)
            if tile is None:
                tile = CrowTile(entry)
                self._tiles[entry.agent_id] = tile
                self.mount(tile)
                mounted += 1
            else:
                if tile.entry != entry:
                    updated += 1
                tile.update_entry(entry)
        self._order = new_ids
        self._resize_grid(len(new_ids))
        return new_ids, mounted, removed, updated

    def _resize_grid(self, count: int) -> None:
        cols = min(GRID_TARGET_COLS, max(1, count))
        rows = max(1, (count + cols - 1) // cols)
        self._cols = cols
        self.styles.grid_size_columns = cols
        self.styles.grid_size_rows = rows

    def _focused_tile_idx(self) -> int | None:
        focused = self.app.focused
        if not isinstance(focused, CrowTile):
            return None
        try:
            return self._order.index(focused.entry.agent_id)
        except ValueError:
            return None

    def _focus_idx(self, idx: int) -> None:
        if 0 <= idx < len(self._order):
            tile = self._tiles.get(self._order[idx])
            if tile is not None:
                tile.focus()

    def action_move_left(self) -> None:
        idx = self._focused_tile_idx()
        if idx is not None and idx % self._cols > 0:
            self._focus_idx(idx - 1)

    def action_move_right(self) -> None:
        idx = self._focused_tile_idx()
        if idx is not None and idx % self._cols < self._cols - 1 and idx + 1 < len(self._order):
            self._focus_idx(idx + 1)

    def action_move_up(self) -> None:
        idx = self._focused_tile_idx()
        if idx is not None and idx - self._cols >= 0:
            self._focus_idx(idx - self._cols)

    def action_move_down(self) -> None:
        idx = self._focused_tile_idx()
        if idx is not None and idx + self._cols < len(self._order):
            self._focus_idx(idx + self._cols)

    def tile_for(self, agent_id: str) -> CrowTile | None:
        return self._tiles.get(agent_id)

    @property
    def order(self) -> list[str]:
        return list(self._order)


class CrowsView(Container):
    """Crows place — wall mode + enlarged mode."""

    DEFAULT_CSS = """
    CrowsView {
        height: 1fr;
        width: 1fr;
        layout: horizontal;
    }
    CrowsView > CrowRosterList {
        height: 1fr;
        width: 30%;
        min-width: 28;
        border: solid $border;
    }
    CrowsView > TailWall {
        height: 1fr;
        width: 1fr;
        border: solid $border;
    }
    CrowsView > PaneMirror { height: 1fr; }
    """

    BINDINGS = [
        Binding("escape", "back_to_wall", "Wall", show=False),
        Binding("q", "back_to_wall", "Wall", show=False),
        Binding("ctrl+h", "hide_focused_tile", "Hide pane", show=False),
    ]

    enlarged_agent_id: reactive[str | None] = reactive(None)

    class TileSelected(Message):
        """Posted whenever the focused tile changes."""

        def __init__(self, entry: CrowEntry) -> None:
            self.entry = entry
            super().__init__()

    def __init__(
        self,
        perf_log: PerfLog | None = None,
        *,
        capture_pane: CapturePaneFn | None = None,
        prefs_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._perf = perf_log
        self._capture_pane = capture_pane
        self._prefs_path = prefs_path
        self._wall = TailWall()
        self._roster = CrowRosterList()
        self._mirror = PaneMirror(perf=self._perf, capture_pane=capture_pane)
        self._entries_by_id: dict[str, CrowEntry] = {}
        self._invalidation_key: str | None = None
        self._last_focused_agent_id: str | None = None
        self._roster.border_title = "agents"
        self._wall.border_title = "tails"

    @property
    def invalidation_key(self) -> str | None:
        return self._invalidation_key

    @property
    def roster(self) -> CrowRosterList:
        return self._roster

    @property
    def wall(self) -> TailWall:
        return self._wall

    def compose(self) -> ComposeResult:
        yield self._roster
        yield self._wall
        yield self._mirror

    def on_mount(self) -> None:
        if self._prefs_path is not None:
            self._roster.set_prefs_path(self._prefs_path)
        self._apply_mode()

    def render_from_snapshot(self, snapshot: CrowSnapshot) -> None:
        """Reconcile the wall from a service snapshot."""
        self._invalidation_key = snapshot.invalidation_key
        entries = entries_from_snapshot(snapshot)
        self._entries_by_id = {e.agent_id: e for e in entries}
        self._roster.reconcile(entries)
        wall_entries = self._visible_wall_entries()
        perf = self._perf
        if perf is not None and perf.enabled:
            with perf.span("tui.crows.reconcile") as dyn:
                _order, m, r, u = self._wall.reconcile(wall_entries)
                dyn["mounted"] = m
                dyn["removed"] = r
                dyn["updated"] = u
        else:
            self._wall.reconcile(wall_entries)
        if self.enlarged_agent_id is not None and self.enlarged_agent_id not in self._entries_by_id:
            self.enlarged_agent_id = None
        self._apply_mode()
        if self.enlarged_agent_id is not None:
            e = self._entries_by_id[self.enlarged_agent_id]
            self._mirror.set_session(e.session)
            self._mirror.border_title = f"{e.ticket_id or '—'} · {e.ticket_title or e.harness}"

    def _visible_wall_entries(self) -> list[CrowEntry]:
        visible = self._roster.pane_visible
        return [entry for entry in self._entries_by_id.values() if entry.agent_id in visible]

    def visible_wall_chat_targets(self) -> tuple[list[str], dict[str, CrowEntry]]:
        """Tail-wall order and entries for pane-visible crows (chat-target cycling)."""
        order = list(self._wall.order)
        entries = {
            agent_id: self._entries_by_id[agent_id]
            for agent_id in order
            if agent_id in self._entries_by_id
        }
        return order, entries

    async def refresh_tails(self) -> None:
        """Capture last-N lines for every visible tile, in parallel."""
        perf = self._perf
        if self.enlarged_agent_id is not None:
            n_tiles = 1
            if perf is not None and perf.enabled:
                with perf.span("tui.crows.refresh_tails", n_tiles=n_tiles):
                    await self._mirror.refresh_pane()
                return
            await self._mirror.refresh_pane()
            return
        tasks = []
        for agent_id in self._wall.order:
            entry = self._entries_by_id.get(agent_id)
            tile = self._wall.tile_for(agent_id)
            if entry is None or tile is None or not entry.session:
                if tile is not None:
                    tile.set_tail("(no session)")
                continue
            tasks.append(self._capture_for_tile(tile, entry.session))
        n_tiles = len(tasks)
        if perf is not None and perf.enabled:
            with perf.span("tui.crows.refresh_tails", n_tiles=n_tiles):
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
            return
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _capture_for_tile(self, tile: CrowTile, session: str) -> None:
        perf = self._perf
        capture = self._capture_pane
        if capture is None:
            tile.set_tail("(no capture)")
            return

        n_lines = TILE_LINES_PARSED if not tile.raw_mode else TILE_LINES_RAW

        async def _run() -> None:
            try:
                text = await asyncio.wait_for(
                    capture(session, n_lines),
                    timeout=CAPTURE_TIMEOUT_S,
                )
            except (PaneCaptureError, asyncio.TimeoutError):
                tile.set_tail("(session vanished)")
                return
            if tile.raw_mode:
                tile.set_tail(text)
            else:
                turns = _parse_tile_text(text, tile.entry.harness)
                tile.set_parsed(turns)

        if perf is not None and perf.enabled:
            with perf.span("tui.crows.capture_tile", session=session):
                await _run()
            return
        await _run()

    def enlarge(self, agent_id: str) -> bool:
        entry = self._entries_by_id.get(agent_id)
        if entry is None:
            return False
        self.enlarged_agent_id = agent_id
        self._mirror.set_session(entry.session)
        self._mirror.border_title = f"{entry.ticket_id or '—'} · {entry.ticket_title or entry.harness}"
        self._apply_mode()
        return True

    def action_back_to_wall(self) -> None:
        if self.enlarged_agent_id is None:
            return
        previous = self.enlarged_agent_id
        self.enlarged_agent_id = None
        self._apply_mode()
        tile = self._wall.tile_for(previous)
        if tile is not None:
            tile.focus()

    def action_hide_focused_tile(self) -> None:
        self.hide_focused_tile()

    def hide_focused_tile(self) -> bool:
        focused = self.app.focused
        if not isinstance(focused, CrowTile):
            return False
        agent_id = focused.entry.agent_id
        if not self._roster.hide_agent(agent_id):
            return False
        if not self._roster.focus_agent(agent_id):
            self._roster.focus()
        return True

    def _apply_mode(self) -> None:
        enlarged = self.enlarged_agent_id is not None
        self._mirror.display = enlarged
        if enlarged:
            self._wall.display = False
            self._roster.display = False
        else:
            self._roster.display = True
            self._wall.display = bool(self._roster.pane_visible)

    def focus_last_tile(self) -> bool:
        """Restore focus to the most recently focused tile, if still present."""
        if self._last_focused_agent_id is not None:
            tile = self._wall.tile_for(self._last_focused_agent_id)
            if tile is not None:
                tile.focus()
                return True
        return False

    def focus_first_tile(self) -> bool:
        if not self._wall.order:
            return False
        tile = self._wall.tile_for(self._wall.order[0])
        if tile is None:
            return False
        tile.focus()
        return True

    def focus_roster(self) -> bool:
        if not self._roster.display:
            return False
        if self._roster.focus_first_row():
            return True
        self._roster.focus()
        return True

    def roster_add_rogue(self, agent_id: str) -> None:
        """Mark a newly spawned rogue crow as favorite and pane-visible."""
        self._roster.add_rogue(agent_id)

    def on_crow_tile_highlighted(self, event: CrowTile.Highlighted) -> None:
        self._last_focused_agent_id = event.entry.agent_id
        self.post_message(self.TileSelected(event.entry))

    def on_crow_tile_opened(self, event: CrowTile.Opened) -> None:
        self.enlarge(event.entry.agent_id)

    def on_crow_roster_list_pane_visibility_changed(
        self,
        event: CrowRosterList.PaneVisibilityChanged,
    ) -> None:
        wall_entries = [
            entry for entry in self._entries_by_id.values() if entry.agent_id in event.visible
        ]
        self._wall.reconcile(wall_entries)
        self._apply_mode()

    def on_crow_tile_view_toggled(self, event: CrowTile.ViewToggled) -> None:
        """On toggle to parsed mode, trigger an immediate re-capture for that tile."""
        if event.raw_mode:
            return  # Switched back to raw — next refresh will repopulate.
        session = event.entry.session
        tile = self._wall.tile_for(event.entry.agent_id)
        if tile is None or not session:
            return
        self.run_worker(
            self._capture_for_tile(tile, session),
            exclusive=False,
            group="crow_tile_parsed",
        )

    def on_crow_roster_list_crow_selected(self, event: CrowRosterList.CrowSelected) -> None:
        entry = self._entries_by_id.get(event.agent_id)
        if entry is not None:
            self.post_message(self.TileSelected(entry))
