"""Crows place — tail-wall of in-flight crow tiles, enlarge-to-mirror.

VISION.md §3.1 / §3.3 / JC-5: the Crows place is a grid of small-multiple
tiles, one per in-flight crow, each showing the last N lines of that
crow's tmux pane, a ticket-id + title header, and a border colored by
client-side health. Selecting a tile enlarges it into a full pane mirror
in place; ESC or `q` returns to the wall.

Tiles render from :class:`~murder.app.service.client_api.CrowSnapshot`
fetched over the service bus. Pane tails use :meth:`~murder.app.tui.client.TuiRuntimeClient.capture_pane`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from textual import events
from textual.actions import SkipAction
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Grid, ScrollableContainer
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import RichLog, Static

from murder.app.service.client_api import CrowSessionSummary, CrowSnapshot
from murder.app.tui.cc_multiple_choice_wizard import CCMultipleChoiceWizard
from murder.app.tui.crow_health import Health, classify, is_stuck
from murder.app.tui.live_log import LiveRichLog
from murder.app.tui.pane_capture import CapturePaneFn, PaneCaptureError
from murder.app.tui.pane_mirror import PaneMirror
from murder.app.tui.perf_log import PerfLog
from murder.llm.harnesses.choice_prompt import ChoiceOption, MultipleChoicePrompt
from murder.llm.harnesses.transcripts import SEGMENT_TYPES, supports_harness

TILE_LINES_RAW = 40
"""Raw-mode tail: last N lines of pane; large enough to show the input box and permission prompts."""

TILE_LINES_PARSED = 400
"""Parsed-mode capture: enough history for wrapped panes and model-switch chatter."""

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

_CROW_PREFIX_RE = re.compile(r"^murder_[^_]+_crow_")
_KNOWN_HARNESS_ALIASES = {
    "agv",
    "antigrav",
    "antigravity",
    "claude",
    "claude_code",
    "codex",
    "cursor",
    "pi",
}


def _short_display_name(raw: str) -> str:
    """Strip project/template prefix, yielding harness+role+id."""
    m = _CROW_PREFIX_RE.match(raw)
    return raw[m.end():] if m else raw


@dataclass(frozen=True)
class CrowDisplayLabels:
    """Compact UI labels for one crow across roster and tile views."""

    name: str
    harness: str
    model: str
    is_rogue: bool


def _display_harness(raw: str) -> str:
    kind = raw.strip().lower()
    return {
        "antigrav": "agv",
        "antigravity": "agv",
        "claude": "claude",
        "claude_code": "claude",
        "codex": "codex",
        "cursor": "cursor",
        "pi": "pi",
    }.get(kind, kind or "—")


def _compact_model(raw: str | None, *, limit: int = 18) -> str:
    model = str(raw or "").strip()
    if not model:
        return "—"
    if "/" in model:
        model = model.rsplit("/", 1)[-1]
    if len(model) <= limit:
        return model
    return model[: limit - 1] + "…"


def _display_name(raw: str, harness: str = "") -> str:
    short = _short_display_name(raw).strip()
    if not short:
        return "crow"
    for marker in ("_rogue_", "-rogue-"):
        if marker in short:
            _prefix, suffix = short.split(marker, 1)
            return suffix or short
    for prefix in ("rogue_", "rogue-"):
        if short.startswith(prefix):
            short = short[len(prefix) :]
            break
    harness_aliases = {
        harness.strip().lower(),
        _display_harness(harness),
    } | _KNOWN_HARNESS_ALIASES
    for alias in sorted((a for a in harness_aliases if a), key=len, reverse=True):
        for sep in ("_", "-"):
            token = f"{alias}{sep}"
            if short.startswith(token):
                trimmed = short[len(token) :]
                if trimmed:
                    return trimmed
    return short


def _is_rogue_entry(entry: CrowEntry) -> bool:
    for raw in (entry.session, entry.agent_id):
        text = str(raw or "").strip().lower()
        if not text:
            continue
        if "_rogue_" in text or "-rogue-" in text or text.startswith(("rogue_", "rogue-")):
            return True
    return False


def _crow_display_labels(entry: CrowEntry) -> CrowDisplayLabels:
    raw_name = entry.session or entry.agent_id or ""
    return CrowDisplayLabels(
        name=_display_name(raw_name, entry.harness) or "crow",
        harness=_display_harness(entry.harness),
        model=_compact_model(entry.model),
        is_rogue=_is_rogue_entry(entry),
    )


def crow_title_label(entry: CrowEntry) -> str:
    labels = _crow_display_labels(entry)
    parts = [labels.name, labels.harness]
    if labels.model != "—":
        parts.append(labels.model)
    if labels.is_rogue:
        parts.append("rogue")
    return " ".join(parts)

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
    model: str | None = None


class CrowRosterRow(Widget):
    """Two-line roster entry for one crow."""

    DEFAULT_CSS = """
    CrowRosterRow {
        height: auto;
        padding: 0 1;
        border-left: tall $border;
        background: $surface;
    }
    CrowRosterRow.-health-red    { border-left: tall $crow-health-red; }
    CrowRosterRow.-health-yellow { border-left: tall $crow-health-yellow; }
    CrowRosterRow.-health-green  { border-left: tall $crow-health-green; }
    CrowRosterRow.-health-neutral{ border-left: tall $crow-health-neutral; }
    CrowRosterRow:focus {
        background: $pane-focus 15%;
        border-left: tall $pane-focus;
    }
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
        labels = _crow_display_labels(e)
        star = "★ " if self._favorite else "  "
        eye = "[pane]" if self._pane_visible else ""
        ticket = f"[{e.ticket_id}]" if e.ticket_id else ""
        status_chip = e.status.upper()
        line1_parts = [f"{star}{labels.name}", f"{status_chip:<8}"]
        if ticket:
            line1_parts.append(ticket)
        if eye:
            line1_parts.append(eye)
        self._line1.update("  ".join(line1_parts))

        if self._kill_pending:
            self._line2.update("  murder this crow? [m / ctrl+m = confirm  ·  any other key = cancel]")
        else:
            activity = (e.ticket_title or "").strip() or labels.name
            meta = [labels.harness]
            if labels.model != "—":
                meta.append(labels.model)
            if labels.is_rogue:
                meta.append("(rogue)")
            meta_text = " · ".join(meta)
            suffix = f"  ·  {meta_text}" if meta_text else ""
            self._line2.update(f"  doing: {activity}{suffix}")

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

    class KillRequested(Message):
        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id
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

    def rename_rogue(self, old_agent_id: str, new_agent_id: str) -> None:
        if old_agent_id in self._favorites:
            self._favorites.discard(old_agent_id)
            self._favorites.add(new_agent_id)
        if old_agent_id in self._pane_visible:
            self._pane_visible.discard(old_agent_id)
            self._pane_visible.add(new_agent_id)
        if self._kill_pending == old_agent_id:
            self._kill_pending = None
        self._save_favorites()
        row = self._rows.pop(old_agent_id, None)
        if row is not None:
            self._rows[new_agent_id] = row
            updated_entry = replace(row.entry, agent_id=new_agent_id, ticket_title=new_agent_id)
            if old_agent_id in self._order:
                self._order = [
                    new_agent_id if agent_id == old_agent_id else agent_id
                    for agent_id in self._order
                ]
            self._update_row(row, updated_entry)

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
            self.post_message(self.KillRequested(agent_id))
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
        model=session.model,
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


def _segment_text(segment: Mapping[str, Any]) -> tuple[str, str] | None:
    seg_type = segment.get("type")
    if seg_type == "user":
        text = segment.get("text")
        return ("user", text) if isinstance(text, str) and text.strip() else None
    if seg_type == "assistant":
        text = segment.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        return ("assistant", text)
    if seg_type == "tool_call":
        title = str(segment.get("title") or "").strip()
        if not title:
            return None
        parts = [title]
        tool_input = segment.get("input")
        if isinstance(tool_input, str) and tool_input.strip():
            parts.append(f"$ {tool_input}")
        result = segment.get("result")
        if isinstance(result, str) and result.strip():
            parts.append(result)
        if segment.get("elided"):
            parts.append("[collapsed]")
        return ("tool", "\n".join(parts))
    if seg_type == "plan_update":
        title = str(segment.get("title") or "").strip()
        items = segment.get("items")
        if not title or not isinstance(items, list):
            return None
        lines = [title]
        for item in items:
            if not isinstance(item, Mapping):
                continue
            marker = "x" if item.get("done") else " "
            text = str(item.get("text") or "").strip()
            if text:
                lines.append(f"[{marker}] {text}")
        return ("plan", "\n".join(lines))
    if seg_type == "agent_event":
        name = str(segment.get("name") or "").strip()
        status = str(segment.get("status") or "").strip()
        elapsed = str(segment.get("elapsed") or "").strip()
        parts = [part for part in (status, name, elapsed) if part]
        return ("agent", " · ".join(parts)) if parts else None
    if seg_type == "choice_prompt":
        question = str(segment.get("question") or "").strip()
        options = segment.get("options")
        if not question:
            return None
        lines = [question]
        if segment.get("answered"):
            chosen = segment.get("chosen")
            if isinstance(options, list):
                for option in options:
                    if not isinstance(option, Mapping):
                        continue
                    if option.get("number") != chosen:
                        continue
                    label = str(option.get("label") or "").strip()
                    if label:
                        lines.append(f"selected: {chosen}. {label}")
                    break
            return ("prompt", "\n".join(lines))
        if isinstance(options, list):
            for option in options:
                if not isinstance(option, Mapping):
                    continue
                number = option.get("number")
                label = str(option.get("label") or "").strip()
                if label:
                    lines.append(f"{number}. {label}")
        return ("prompt", "\n".join(lines))
    if seg_type == "notice":
        message = str(segment.get("message") or segment.get("text") or "").strip()
        severity = str(segment.get("severity") or "").strip()
        if not message:
            return None
        return ("notice", f"{severity}: {message}" if severity else message)
    if seg_type not in SEGMENT_TYPES:
        logger.warning("crow transcript render: dropping unknown segment type %r", seg_type)
    return None


def doc_to_chat_turns(doc: Mapping[str, Any]) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    segments = doc.get("segments")
    if not isinstance(segments, list):
        return turns
    for segment in segments:
        if not isinstance(segment, Mapping):
            continue
        rendered = _segment_text(segment)
        if rendered is not None:
            turns.append(rendered)
    return turns


def _segment_to_choice_prompt(segment: Mapping[str, Any]) -> MultipleChoicePrompt | None:
    if segment.get("type") != "choice_prompt" or segment.get("answered"):
        return None
    question = str(segment.get("question") or "").strip()
    options_raw = segment.get("options")
    if not question or not isinstance(options_raw, list):
        return None
    options: list[ChoiceOption] = []
    for option in options_raw:
        if not isinstance(option, Mapping):
            continue
        number = option.get("number")
        label = str(option.get("label") or "").strip()
        if not isinstance(number, int) or not label:
            continue
        description = str(option.get("description") or "").strip()
        options.append(ChoiceOption(number=number, label=label, description=description))
    if not options:
        return None
    selected_number = segment.get("selected")
    selected_index = 0
    if isinstance(selected_number, int):
        for idx, option in enumerate(options):
            if option.number == selected_number:
                selected_index = idx
                break
    return MultipleChoicePrompt(
        question=question,
        options=tuple(options),
        selected_index=selected_index,
        footer=str(segment.get("footer") or "").strip(),
    )


def _live_choice_prompt(doc: Mapping[str, Any]) -> MultipleChoicePrompt | None:
    if doc.get("state") != "awaiting_approval":
        return None
    segments = doc.get("segments")
    if not isinstance(segments, list):
        return None
    for segment in reversed(segments):
        if not isinstance(segment, Mapping):
            continue
        prompt = _segment_to_choice_prompt(segment)
        if prompt is not None:
            return prompt
    return None


def _last_user_summary(doc: Mapping[str, Any]) -> str:
    segments = doc.get("segments")
    if not isinstance(segments, list):
        return ""
    for segment in reversed(segments):
        if isinstance(segment, Mapping) and segment.get("type") == "user":
            text = segment.get("text")
            if isinstance(text, str):
                return text.splitlines()[0].strip()
    return ""


class CrowTile(Container):
    """One tile in the wall: header + raw tail OR parsed chat transcript."""

    DEFAULT_CSS = """
    CrowTile {
        border: solid $border-blurred;
        padding: 0 1;
        height: 1fr;
        width: 1fr;
        layout: vertical;
    }
    CrowTile:focus,
    CrowTile:focus-within { border: heavy $pane-focus; }
    CrowTile.-chat-target,
    CrowTile.-chat-target:focus,
    CrowTile.-chat-target:focus-within { border: heavy $accent; }
    CrowTile > RichLog,
    CrowTile > ChatLog {
        height: 1fr;
        width: 1fr;
        background: transparent;
        scrollbar-size-vertical: 1;
        scrollbar-color: $scrollbar 50%;
        scrollbar-color-hover: $scrollbar 70%;
        scrollbar-color-active: $scrollbar 85%;
        scrollbar-background: transparent;
        scrollbar-background-hover: $scrollbar-background 20%;
        scrollbar-background-active: $scrollbar-background 30%;
    }
    CrowTile > ChatLog {
        overflow-x: hidden;
        overflow-y: auto;
        border: none;
        padding: 0;
    }
    """

    can_focus = True
    BINDINGS = [
        Binding("ctrl+o", "open", "Enlarge", show=False),
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

    class ChoicePromptConfirmed(Message):
        def __init__(self, entry: CrowEntry, option_number: int, label: str) -> None:
            self.entry = entry
            self.option_number = option_number
            self.label = label
            super().__init__()

    def __init__(self, entry: CrowEntry) -> None:
        super().__init__()
        self._entry = entry
        # Default to parsed mode when a parser is available for this harness;
        # fall back to raw mode for harnesses with no transcript parser so the
        # tile is never a blank screen on first render.
        self._raw_mode = not supports_harness(entry.harness or "")
        self._last_turns: list[tuple[str, str]] = []
        self._parsed_loaded = False
        self._last_render_width = 0
        self._last_user_msg: str = ""
        self._raw_log = LiveRichLog(
            highlight=False,
            markup=False,
            min_width=1,
            wrap=True,
        )
        # Import here to avoid requiring planning_mode_widgets at module load time.
        from murder.app.tui.planning_mode_widgets import ChatLog as _ChatLog

        self._chat_log = _ChatLog(agent_label=entry.harness or "agent")
        self._choice_wizard: CCMultipleChoiceWizard | None = None
        self._chat_targeted = False

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
        self._raw_log.display = self._raw_mode
        self._chat_log.display = not self._raw_mode

    def on_key(self, event: events.Key) -> None:
        if self._raw_mode:
            return
        # Call the scroll actions directly (not via the binding dispatcher), so
        # suppress the SkipAction they raise when the transcript isn't scrollable
        # — otherwise a stray j/k while the tile is focused crashes the TUI.
        if event.key in ("j", "down"):
            with contextlib.suppress(SkipAction):
                self._chat_log.action_scroll_down()
            event.stop()
        elif event.key in ("k", "up"):
            with contextlib.suppress(SkipAction):
                self._chat_log.action_scroll_up()
            event.stop()

    def update_entry(self, entry: CrowEntry) -> None:
        """Reconcile after a snapshot refresh; rebuild border + header in place."""
        if entry.harness != self._entry.harness:
            self._raw_mode = not supports_harness(entry.harness or "")
            self._parsed_loaded = False
            self._last_turns = []
            self._last_render_width = 0
            self._last_user_msg = ""
        self._entry = entry
        self._apply_entry()

    def set_chat_targeted(self, active: bool) -> None:
        self._chat_targeted = active
        self.set_class(active, "-chat-target")

    def set_tail(self, text: str) -> None:
        """Update the raw log view (called on each refresh tick)."""
        def _write_tail() -> None:
            for line in text.splitlines():
                self._raw_log.write(line)

        self._raw_log.replace_lines(_write_tail)

    def set_parsed_doc(self, doc: Mapping[str, Any], harness_kind: str = "") -> None:
        """Update the parsed chat log.

        A RichLog renders and caches each line's Strip at the widget's width when
        ``write()`` is called, and does not reflow on resize. A parse can land
        while the ChatLog is still ``display:false`` / pre-layout (size 0 → it
        renders at ``min_width`` 1), e.g. right after a ctrl+y toggle reveals the
        tile. Caching that narrow render paints the transcript as a column of
        single characters (or blank) that never recovers.

        So: defer until the ChatLog has a real width, then re-render whenever the
        turns change *or* the width changed since the last render (a later resize
        must re-flow the cached lines). The refresh tick re-invokes this, so a
        deferred first parse renders on the next tick once layout has run.
        """
        width = self._chat_log.size.width
        if width <= 1:
            return  # not laid out yet — avoid caching a min-width render
        live_prompt = _live_choice_prompt(doc)
        turns = doc_to_chat_turns(doc)
        if self._parsed_loaded and turns == self._last_turns and width == self._last_render_width:
            self._sync_choice_prompt(live_prompt)
            return
        self._parsed_loaded = True
        self._last_turns = turns
        self._last_render_width = width
        self._chat_log.set_turns(turns)
        self._sync_choice_prompt(live_prompt)
        if not turns:
            kind = harness_kind or self._entry.harness or "unknown"
            self._chat_log.add_status(f"(no parsed transcript visible yet for '{kind}')")
        new_msg = _last_user_summary(doc)
        if new_msg != self._last_user_msg:
            self._last_user_msg = new_msg
            self._apply_entry()

    def set_parsed_status(self, text: str) -> None:
        self._parsed_loaded = True
        self._last_turns = []
        self._sync_choice_prompt(None)
        self._chat_log.replace_transcript([], status=text)

    def action_toggle_view(self) -> None:
        self._raw_mode = not self._raw_mode
        self._raw_log.display = self._raw_mode
        self._chat_log.display = not self._raw_mode and self._choice_wizard is None
        if self._choice_wizard is not None:
            self._choice_wizard.display = not self._raw_mode
        self.post_message(self.ViewToggled(self._entry, self._raw_mode))

    def set_raw_mode(self, value: bool) -> None:
        # Raw-only tiles (unsupported harness) stay raw regardless.
        target = value or not supports_harness(self._entry.harness or "")
        if self._raw_mode != target:
            self.action_toggle_view()

    def on_cc_multiple_choice_wizard_confirmed(
        self,
        event: CCMultipleChoiceWizard.Confirmed,
    ) -> None:
        event.stop()
        self.post_message(self.ChoicePromptConfirmed(self._entry, event.option_number, event.label))

    def on_cc_multiple_choice_wizard_cancelled(
        self,
        event: CCMultipleChoiceWizard.Cancelled,
    ) -> None:
        event.stop()

    def _sync_choice_prompt(self, prompt: MultipleChoicePrompt | None) -> None:
        if prompt is None:
            wizard = self._choice_wizard
            if wizard is not None:
                wizard.remove()
                self._choice_wizard = None
            self._chat_log.display = not self._raw_mode
            return
        wizard = self._choice_wizard
        if wizard is None:
            wizard = CCMultipleChoiceWizard(prompt)
            self._choice_wizard = wizard
            self.mount(wizard)
        else:
            wizard.update_prompt(prompt)
        wizard.display = not self._raw_mode
        self._chat_log.display = False
        if wizard.display and self.has_focus:
            wizard.focus()

    def _apply_entry(self) -> None:
        e = self._entry
        self.border_title = crow_title_label(e)
        subtitle = self._last_user_msg.strip()
        if not subtitle:
            parts = [e.status.upper()]
            if e.ticket_id:
                parts.append(f"[{e.ticket_id}]")
            subtitle = "  ".join(parts)
        self.border_subtitle = subtitle
        for h in Health:
            self.remove_class(f"-health-{h.value}")
        self.add_class(f"-health-{e.health.value}")
        self.set_class(self._chat_targeted, "-chat-target")

    def on_focus(self) -> None:
        if self._choice_wizard is not None and self._choice_wizard.display:
            self._choice_wizard.focus()
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
        self._chat_target_agent_id: str | None = None

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
                self.styles.grid_columns = "1fr"
                self.styles.grid_rows = "1fr"
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
            tile.set_chat_targeted(entry.agent_id == self._chat_target_agent_id)
        self._order = new_ids
        self._resize_grid(len(new_ids))
        return new_ids, mounted, removed, updated

    def set_chat_target(self, agent_id: str | None) -> None:
        self._chat_target_agent_id = agent_id
        for tile_agent_id, tile in self._tiles.items():
            tile.set_chat_targeted(tile_agent_id == agent_id)

    def _resize_grid(self, count: int) -> None:
        if count == 5:
            # 6-column grid: top row = 3 tiles at span 2, bottom row = 2 tiles at span 3
            self._cols = 3
            grid_cols = 6
            rows = 2
            self.styles.grid_size_columns = grid_cols
            self.styles.grid_size_rows = rows
            self.styles.grid_columns = " ".join("1fr" for _ in range(grid_cols))
            self.styles.grid_rows = "1fr 1fr"
            for i, agent_id in enumerate(self._order):
                tile = self._tiles.get(agent_id)
                if tile is not None:
                    tile.styles.column_span = 2 if i < 3 else 3
        elif count == 4:
            self._cols = 2
            self.styles.grid_size_columns = 2
            self.styles.grid_size_rows = 2
            self.styles.grid_columns = "1fr 1fr"
            self.styles.grid_rows = "1fr 1fr"
            for agent_id in self._order:
                tile = self._tiles.get(agent_id)
                if tile is not None:
                    tile.styles.column_span = 1
        else:
            cols = min(GRID_TARGET_COLS, max(1, count))
            rows = max(1, (count + cols - 1) // cols)
            self._cols = cols
            self.styles.grid_size_columns = cols
            self.styles.grid_size_rows = rows
            self.styles.grid_columns = " ".join("1fr" for _ in range(cols))
            self.styles.grid_rows = " ".join("1fr" for _ in range(rows))
            for agent_id in self._order:
                tile = self._tiles.get(agent_id)
                if tile is not None:
                    tile.styles.column_span = 1

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
    def tiles(self) -> list[CrowTile]:
        return list(self._tiles.values())

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
        width: 21%;
        min-width: 22;
        border: solid $border;
    }
    CrowsView > CrowRosterList:focus-within {
        border: heavy $accent;
    }
    CrowsView > TailWall {
        height: 1fr;
        width: 1fr;
        border: solid $border;
    }
    CrowsView > TailWall:focus-within {
        border: heavy $accent;
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

    class KillRequested(Message):
        def __init__(self, agent_id: str) -> None:
            self.agent_id = agent_id
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
        self._chat_target_agent_id: str | None = None
        self._roster_visible: bool = True
        self._conversation_docs: dict[str, Mapping[str, Any]] = {}
        self._roster.border_title = "crows"
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
        self._wall.set_chat_target(self._chat_target_agent_id)
        for agent_id in self._wall.order:
            tile = self._wall.tile_for(agent_id)
            if tile is not None and not tile.raw_mode:
                self._render_parsed_tile(tile)
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

    def set_conversation_doc(self, conversation_id: str, doc: Mapping[str, Any] | None) -> None:
        if doc is None:
            self._conversation_docs.pop(conversation_id, None)
        else:
            self._conversation_docs[conversation_id] = doc
        tile = self._wall.tile_for(conversation_id)
        if tile is not None and not tile.raw_mode:
            self._render_parsed_tile(tile)

    def set_conversation_docs(self, docs: Mapping[str, Mapping[str, Any]]) -> None:
        self._conversation_docs = dict(docs)
        for agent_id in self._wall.order:
            tile = self._wall.tile_for(agent_id)
            if tile is not None and not tile.raw_mode:
                self._render_parsed_tile(tile)

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
            if tile.raw_mode:
                await self._refresh_raw_tile(tile, session)
            else:
                await self._refresh_parsed_tile(tile)

        if perf is not None and perf.enabled:
            with perf.span("tui.crows.capture_tile", session=session):
                await _run()
            return
        await _run()

    async def _refresh_raw_tile(self, tile: CrowTile, session: str) -> None:
        """Raw mode: mirror the pane verbatim via the local capture callback."""
        capture = self._capture_pane
        if capture is None:
            tile.set_tail("(no capture)")
            return
        try:
            text = await asyncio.wait_for(
                capture(session, lines=TILE_LINES_RAW),  # type: ignore[call-arg]
                timeout=CAPTURE_TIMEOUT_S,
            )
        except (PaneCaptureError, asyncio.TimeoutError):
            tile.set_tail("(session vanished)")
            return
        tile.set_tail(text)

    async def _refresh_parsed_tile(self, tile: CrowTile) -> None:
        """Parsed mode: render the TUI's event-sourced conversation projection."""
        self._render_parsed_tile(tile)

    def _render_parsed_tile(self, tile: CrowTile) -> None:
        doc = self._conversation_docs.get(tile.entry.agent_id)
        if doc is None:
            tile.set_parsed_status("(parsed transcript unavailable)")
            return
        tile.set_parsed_doc(doc, str(doc.get("harness") or tile.entry.harness))

    def enlarge(self, agent_id: str) -> bool:
        entry = self._entries_by_id.get(agent_id)
        if entry is None:
            return False
        self.enlarged_agent_id = agent_id
        self._mirror.set_session(entry.session)
        self._mirror.border_title = f"{entry.ticket_id or '—'} · {entry.ticket_title or entry.harness}"
        self._apply_mode()
        return True

    def toggle_roster(self) -> bool:
        """Toggle the crows roster sidebar. Returns new visibility state."""
        self._roster_visible = not self._roster_visible
        self._apply_mode()
        if not self._roster_visible:
            if not self.focus_first_tile():
                self._wall.focus()
        return self._roster_visible

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
            self._roster.display = self._roster_visible
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

    def roster_rename_rogue(self, old_agent_id: str, new_agent_id: str) -> None:
        self._roster.rename_rogue(old_agent_id, new_agent_id)
        entry = self._entries_by_id.pop(old_agent_id, None)
        if entry is not None:
            self._entries_by_id[new_agent_id] = replace(
                entry,
                agent_id=new_agent_id,
                ticket_title=new_agent_id,
            )
        if self._last_focused_agent_id == old_agent_id:
            self._last_focused_agent_id = new_agent_id
        if self._chat_target_agent_id == old_agent_id:
            self._chat_target_agent_id = new_agent_id
            self._wall.set_chat_target(new_agent_id)
        if self.enlarged_agent_id == old_agent_id:
            self.enlarged_agent_id = new_agent_id

    def set_chat_target(self, agent_id: str | None) -> None:
        self._chat_target_agent_id = agent_id
        self._wall.set_chat_target(agent_id)

    def on_crow_roster_list_kill_requested(self, event: CrowRosterList.KillRequested) -> None:
        self.post_message(self.KillRequested(event.agent_id))

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
        """Trigger an immediate re-capture after any parsed↔raw toggle.

        Without this, switching to raw mode shows an empty log until the next
        periodic refresh tick because the raw_log is never written in parsed mode.
        """
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
