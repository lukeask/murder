"""Crows place — tail-wall of in-flight crow tiles, enlarge-to-mirror.

VISION.md §3.1 / §3.3 / JC-5: the Crows place is a grid of small-multiple
tiles, one per in-flight crow, each showing the last N lines of that
crow's tmux pane, a ticket-id + title header, and a border colored by
client-side health. Selecting a tile enlarges it into a full pane mirror
in place; ESC or `q` returns to the wall.

Tiles render from :class:`~murder.service.client_api.CrowSnapshot`
via ``ServiceReadModel``. Pane tails still use ``tmux.capture_pane`` until
pane-content bus subscriptions land.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Grid
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from murder.service.client_api import CrowSessionSummary, CrowSnapshot
from murder.terminal import tmux
from murder.tui.crow_health import Health, classify, is_stuck
from murder.tui.pane_mirror import PaneMirror
from murder.tui.perf_log import PerfLog

TILE_LINES = 8
"""Last N lines of pane each tile shows in the wall."""

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


@dataclass(frozen=True)
class CrowEntry:
    """One tile in the wall, projected from :class:`CrowSessionSummary`."""

    agent_id: str
    ticket_id: str
    ticket_title: str
    harness: str
    status: str
    session: str | None
    health: Health


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
            e.ticket_id,
            e.agent_id,
        )
    )
    return entries


def _entry_from_session(
    session: CrowSessionSummary,
    *,
    now: datetime,
) -> CrowEntry | None:
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


class CrowTile(Container):
    """One tile in the wall: header + last-N lines + health-colored border."""

    DEFAULT_CSS = """
    CrowTile {
        border: solid $border;
        padding: 0 1;
        height: 1fr;
        width: 1fr;
        layout: vertical;
    }
    CrowTile.-health-red    { border: solid red; }
    CrowTile.-health-yellow { border: solid yellow; }
    CrowTile.-health-green  { border: solid green; }
    CrowTile.-health-neutral{ border: solid $border; }
    CrowTile:focus,
    CrowTile:focus-within { border: heavy $accent; }
    CrowTile > Static.tail {
        height: 1fr;
        width: 1fr;
        overflow-y: hidden;
    }
    """

    can_focus = True
    BINDINGS = [
        Binding("enter", "open", "Enlarge", show=False),
        Binding("o", "open", "Enlarge", show=False),
    ]

    class Highlighted(Message):
        def __init__(self, entry: CrowEntry) -> None:
            self.entry = entry
            super().__init__()

    class Opened(Message):
        def __init__(self, entry: CrowEntry) -> None:
            self.entry = entry
            super().__init__()

    def __init__(self, entry: CrowEntry) -> None:
        super().__init__()
        self._entry = entry
        self._tail = Static("", classes="tail", markup=False)

    @property
    def entry(self) -> CrowEntry:
        return self._entry

    def compose(self) -> ComposeResult:
        yield self._tail

    def on_mount(self) -> None:
        self._apply_entry()

    def update_entry(self, entry: CrowEntry) -> None:
        """Reconcile after a snapshot refresh; rebuild border + header in place."""
        self._entry = entry
        self._apply_entry()

    def set_tail(self, text: str) -> None:
        self._tail.update(text)

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
        self.post_message(self.Opened(self._entry))


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
        super().__init__("(no crows yet — kick a ready ticket from Schedule)")


class TailWall(Grid):
    """Grid of CrowTiles. Owns reconciliation against snapshot entries."""

    DEFAULT_CSS = """
    TailWall {
        grid-gutter: 0;
        height: 1fr;
        width: 1fr;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._tiles: dict[str, CrowTile] = {}
        self._order: list[str] = []
        self._empty: _EmptyMessage | None = None

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
        self.styles.grid_size_columns = cols
        self.styles.grid_size_rows = rows

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
        layout: vertical;
    }
    CrowsView > TailWall { height: 1fr; }
    CrowsView > PaneMirror { height: 1fr; }
    """

    BINDINGS = [
        Binding("escape", "back_to_wall", "Wall", show=False),
        Binding("q", "back_to_wall", "Wall", show=False),
    ]

    enlarged_agent_id: reactive[str | None] = reactive(None)

    class TileSelected(Message):
        """Posted whenever the focused tile changes."""

        def __init__(self, entry: CrowEntry) -> None:
            self.entry = entry
            super().__init__()

    def __init__(self, perf_log: PerfLog | None = None) -> None:
        super().__init__()
        self._perf = perf_log
        self._wall = TailWall()
        self._mirror = PaneMirror(perf=self._perf)
        self._entries_by_id: dict[str, CrowEntry] = {}
        self._invalidation_key: str | None = None
        self.border_title = "crows"

    @property
    def invalidation_key(self) -> str | None:
        return self._invalidation_key

    def compose(self) -> ComposeResult:
        yield self._wall
        yield self._mirror

    def on_mount(self) -> None:
        self._apply_mode()

    def render_from_snapshot(self, snapshot: CrowSnapshot) -> None:
        """Reconcile the wall from a service snapshot."""
        self._invalidation_key = snapshot.invalidation_key
        entries = entries_from_snapshot(snapshot)
        self._entries_by_id = {e.agent_id: e for e in entries}
        perf = self._perf
        if perf is not None and perf.enabled:
            with perf.span("tui.crows.reconcile") as dyn:
                _order, m, r, u = self._wall.reconcile(entries)
                dyn["mounted"] = m
                dyn["removed"] = r
                dyn["updated"] = u
        else:
            self._wall.reconcile(entries)
        if self.enlarged_agent_id is not None and self.enlarged_agent_id not in self._entries_by_id:
            self.enlarged_agent_id = None
            self._apply_mode()
            return
        if self.enlarged_agent_id is not None:
            e = self._entries_by_id[self.enlarged_agent_id]
            self._mirror.set_session(e.session)
            self._mirror.border_title = f"{e.ticket_id or '—'} · {e.ticket_title or e.harness}"

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

        async def _run() -> None:
            try:
                text = await asyncio.wait_for(
                    tmux.capture_pane(session, lines=TILE_LINES, perf=perf),
                    timeout=CAPTURE_TIMEOUT_S,
                )
            except (tmux.TmuxError, asyncio.TimeoutError):
                tile.set_tail("(session vanished)")
                return
            lines = text.splitlines()
            if len(lines) > TILE_LINES:
                lines = lines[-TILE_LINES:]
            tile.set_tail("\n".join(lines))

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

    def _apply_mode(self) -> None:
        enlarged = self.enlarged_agent_id is not None
        self._wall.display = not enlarged
        self._mirror.display = enlarged

    def focus_first_tile(self) -> bool:
        if not self._wall.order:
            return False
        tile = self._wall.tile_for(self._wall.order[0])
        if tile is None:
            return False
        tile.focus()
        return True

    def on_crow_tile_highlighted(self, event: CrowTile.Highlighted) -> None:
        self.post_message(self.TileSelected(event.entry))

    def on_crow_tile_opened(self, event: CrowTile.Opened) -> None:
        self.enlarge(event.entry.agent_id)
