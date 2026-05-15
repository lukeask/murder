"""Crows place — tail-wall of in-flight crow tiles, enlarge-to-mirror.

VISION.md §3.1 / §3.3 / JC-5: the Crows place is a grid of small-multiple
tiles, one per in-flight crow, each showing the last N lines of that
crow's tmux pane, a ticket-id + title header, and a border colored by
client-side health. Selecting a tile enlarges it into a full pane mirror
in place; ESC or `q` returns to the wall.

The widget reads from DB snapshots + `tmux.capture_pane` for now. The
data path is the same seam that will swap to `pane_content` / `crow_health`
bus subscriptions when those land (VISION §7.1) — only the source moves.
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Grid
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from murder import tmux
from murder.tui.crow_health import Health, classify
from murder.tui.pane_mirror import PaneMirror

TILE_LINES = 8
"""Last N lines of pane each tile shows in the wall."""

CAPTURE_TIMEOUT_S = 2.0
"""Per-tile tmux capture timeout — a stuck pane must not block the wall."""

GRID_TARGET_COLS = 3
"""Tail-wall packs roughly into this many columns; rows scale by count."""

TERMINAL_TICKET_STATUSES = frozenset({"done", "failed"})
"""Ticket states that indicate the work item is closed."""

FAILED_STALE_AFTER = timedelta(hours=2)
"""Hide failed agents after this long unless their ticket is still active."""


@dataclass(frozen=True)
class CrowEntry:
    """One row in the wall, joined from `agents` + `tickets`."""

    agent_id: str
    role: str
    ticket_id: str | None
    ticket_title: str
    status: str
    session: str | None
    health: Health


def load_crow_entries(db: sqlite3.Connection, *, now: datetime | None = None) -> list[CrowEntry]:
    """Project the DB into one CrowEntry per live agent the wall cares about.

    Excludes `done`/`dead` agents so the wall doesn't fill with corpses.
    Failed agents stay visible while fresh or tied to non-terminal tickets,
    then age out so stale historical failures don't dominate the wall.
    """
    now = now or datetime.now(timezone.utc)
    rows = db.execute(
        """
        SELECT a.agent_id, a.role, a.ticket_id, a.status, a.session,
               a.started_at, a.last_heartbeat_at,
               COALESCE(t.title, '') AS title,
               COALESCE(t.status, '') AS ticket_status
          FROM agents a
          LEFT JOIN tickets t ON t.id = a.ticket_id
         WHERE a.status NOT IN ('done', 'dead')
         ORDER BY
               CASE a.status
                 WHEN 'escalating' THEN 0
                 WHEN 'blocked' THEN 1
                 WHEN 'running' THEN 2
                 WHEN 'idle' THEN 3
                 WHEN 'failed' THEN 4
                 ELSE 5
               END,
               a.started_at DESC,
               a.agent_id
        """
    ).fetchall()
    if not rows:
        return []
    rows = [r for r in rows if _keep_wall_row(r, now=now)]
    if not rows:
        return []

    ticket_ids = [r["ticket_id"] for r in rows if r["ticket_id"]]
    open_by_ticket: dict[str, tuple[int, int]] = {}
    if ticket_ids:
        placeholders = ",".join("?" * len(ticket_ids))
        for esc in db.execute(
            f"""
            SELECT ticket_id, COUNT(*) AS n, MAX(severity) AS max_sev
              FROM escalations
             WHERE resolved = 0 AND ticket_id IN ({placeholders})
             GROUP BY ticket_id
            """,
            ticket_ids,
        ).fetchall():
            open_by_ticket[str(esc["ticket_id"])] = (
                int(esc["n"]),
                int(esc["max_sev"] or 0),
            )

    entries: list[CrowEntry] = []
    for r in rows:
        n, sev = open_by_ticket.get(r["ticket_id"] or "", (0, 0))
        entries.append(
            CrowEntry(
                agent_id=r["agent_id"],
                role=r["role"],
                ticket_id=r["ticket_id"],
                ticket_title=r["title"] or "",
                status=r["status"],
                session=r["session"],
                health=classify(
                    status=r["status"],
                    open_escalations=n,
                    max_severity=sev,
                ),
            )
        )
    return entries


def _keep_wall_row(row: sqlite3.Row, *, now: datetime) -> bool:
    """Return True if this agent row should stay visible on the crows wall."""
    if row["status"] != "failed":
        return True
    ticket_status = str(row["ticket_status"] or "")
    if ticket_status and ticket_status not in TERMINAL_TICKET_STATUSES:
        return True
    last_seen = _parse_iso_ts(row["last_heartbeat_at"]) or _parse_iso_ts(row["started_at"])
    if last_seen is None:
        return True
    age = now - last_seen
    return age <= FAILED_STALE_AFTER


def _parse_iso_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
        """Reconcile after a DB refresh; rebuild border + header in place."""
        self._entry = entry
        self._apply_entry()

    def set_tail(self, text: str) -> None:
        self._tail.update(text)

    def _apply_entry(self) -> None:
        e = self._entry
        ticket = e.ticket_id or "—"
        title = e.ticket_title or e.role
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
    """Grid of CrowTiles. Owns reconciliation against the DB."""

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

    def reconcile(self, entries: list[CrowEntry]) -> list[str]:
        """Make the visible tile set match `entries`. Returns the new agent order."""
        new_ids = [e.agent_id for e in entries]
        # Drop empty placeholder if we now have entries
        if entries and self._empty is not None:
            self._empty.remove()
            self._empty = None
        # Show empty placeholder if we have none
        if not entries:
            for agent_id in list(self._tiles):
                self._tiles.pop(agent_id).remove()
            self._order = []
            if self._empty is None:
                self._empty = _EmptyMessage()
                self.mount(self._empty)
                self.styles.grid_size_columns = 1
                self.styles.grid_size_rows = 1
            return []
        # Remove gone-away tiles
        for agent_id in list(self._tiles):
            if agent_id not in new_ids:
                self._tiles.pop(agent_id).remove()
        # Mount or update remaining
        for entry in entries:
            tile = self._tiles.get(entry.agent_id)
            if tile is None:
                tile = CrowTile(entry)
                self._tiles[entry.agent_id] = tile
                self.mount(tile)
            else:
                tile.update_entry(entry)
        self._order = new_ids
        self._resize_grid(len(new_ids))
        return new_ids

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
    """Crows place — wall mode + enlarged mode.

    Mounts a TailWall and an internal PaneMirror as siblings; flips
    their `display` to switch modes. The mirror reuses the same widget
    the planning view uses for raw tmux, so behavior stays consistent.
    """

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
        """Posted whenever the focused tile changes; the app updates the
        planning-side mirror's session for parity with the old behavior."""

        def __init__(self, entry: CrowEntry) -> None:
            self.entry = entry
            super().__init__()

    def __init__(self) -> None:
        super().__init__()
        self._wall = TailWall()
        self._mirror = PaneMirror()
        self._entries_by_id: dict[str, CrowEntry] = {}
        self.border_title = "crows"

    def compose(self) -> ComposeResult:
        yield self._wall
        yield self._mirror

    def on_mount(self) -> None:
        self._apply_mode()

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        entries = load_crow_entries(db)
        self._entries_by_id = {e.agent_id: e for e in entries}
        self._wall.reconcile(entries)
        # If the currently-enlarged crow disappeared, fall back to wall.
        if self.enlarged_agent_id is not None and self.enlarged_agent_id not in self._entries_by_id:
            self.enlarged_agent_id = None
            self._apply_mode()
            return
        # Refresh the enlarged tile's mirror session in case it changed.
        if self.enlarged_agent_id is not None:
            e = self._entries_by_id[self.enlarged_agent_id]
            self._mirror.set_session(e.session)
            self._mirror.border_title = f"{e.ticket_id or '—'} · {e.ticket_title or e.role}"

    async def refresh_tails(self) -> None:
        """Capture last-N lines for every visible tile, in parallel.

        Per-tile timeout + gather so one stuck pane can't block input.
        """
        if self.enlarged_agent_id is not None:
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
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _capture_for_tile(self, tile: CrowTile, session: str) -> None:
        try:
            text = await asyncio.wait_for(
                tmux.capture_pane(session, lines=TILE_LINES),
                timeout=CAPTURE_TIMEOUT_S,
            )
        except (tmux.TmuxError, asyncio.TimeoutError):
            tile.set_tail("(session vanished)")
            return
        # Show only the last TILE_LINES non-empty trailing lines; capture_pane
        # may return blank padding at the bottom of a fresh pane.
        lines = text.splitlines()
        if len(lines) > TILE_LINES:
            lines = lines[-TILE_LINES:]
        tile.set_tail("\n".join(lines))

    # ── mode transitions ──────────────────────────────────────────────────

    def enlarge(self, agent_id: str) -> bool:
        entry = self._entries_by_id.get(agent_id)
        if entry is None:
            return False
        self.enlarged_agent_id = agent_id
        self._mirror.set_session(entry.session)
        self._mirror.border_title = f"{entry.ticket_id or '—'} · {entry.ticket_title or entry.role}"
        self._apply_mode()
        return True

    def action_back_to_wall(self) -> None:
        if self.enlarged_agent_id is None:
            return
        previous = self.enlarged_agent_id
        self.enlarged_agent_id = None
        self._apply_mode()
        # Restore focus to the tile we were inspecting if it's still around.
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
