"""Header bar: project identity, view tabs, in-flight crows, attention counts."""

from __future__ import annotations

from textual.widgets import Static

from murder.service.client_api import CrowSnapshot, DispatchSnapshot
from murder.tui.crows_view import CrowEntry, _short_display_name, entries_from_snapshot

_ATTENTION_STATUSES = ("blocked", "failed")
_HEADER_CROW_ID_LIMIT = 3
_HEADER_NAME_MAX = 12
_VIEW_TABS = (
    ("planning", "planning"),
    ("crows", "crows"),
    ("schedule", "dispatch"),
)


def crow_display_id(entry: CrowEntry) -> str:
    """Ticket id for bound crows; truncated rogue name otherwise."""
    if entry.ticket_id:
        return entry.ticket_id
    name = _short_display_name(entry.session or entry.agent_id)
    if len(name) > _HEADER_NAME_MAX:
        return name[:_HEADER_NAME_MAX] + "…"
    return name


def format_inflight_segment(entries: list[CrowEntry]) -> str:
    """Render ▶N id… with overflow +K when more than three crows."""
    if not entries:
        return ""
    total = len(entries)
    ids = [crow_display_id(entry) for entry in entries[:_HEADER_CROW_ID_LIMIT]]
    parts = [f"▶{total}", *ids]
    overflow = total - _HEADER_CROW_ID_LIMIT
    if overflow > 0:
        parts.append(f"+{overflow}")
    return " ".join(parts)


def format_attention_segments(counts: dict[str, int]) -> list[str]:
    """Blocked/failed ticket counts; omit zeros."""
    segments: list[str] = []
    blocked = counts.get("blocked", 0)
    failed = counts.get("failed", 0)
    if blocked:
        segments.append(f"⚠{blocked}")
    if failed:
        segments.append(f"✗{failed}")
    return segments


def format_view_tabs(view: str, accent: str | None) -> str:
    """Plain view labels; active tab bold + theme accent when available."""
    labels: list[str] = []
    for key, label in _VIEW_TABS:
        if key == view:
            if accent:
                hex_color = accent if accent.startswith("#") else f"#{accent}"
                labels.append(f"[b {hex_color}]{label}[/]")
            else:
                labels.append(f"[b]{label}[/]")
        else:
            labels.append(label)
    return "  ".join(labels)


class Header(Static):
    """Project name, view tabs, live crows, and attention counts."""

    DEFAULT_CSS = """
    Header {
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(self, project: str) -> None:
        super().__init__("murder")
        self.project = project
        self._counts: dict[str, int] = {s: 0 for s in _ATTENTION_STATUSES}
        self._view = "planning"
        self._crow_snapshot: CrowSnapshot | None = None

    def refresh_from_snapshot(
        self,
        snapshot: DispatchSnapshot,
        *,
        crow_snapshot: CrowSnapshot | None = None,
    ) -> None:
        counts = {s: 0 for s in _ATTENTION_STATUSES}
        for ticket in snapshot.tickets:
            key = ticket.status.value
            if key in counts:
                counts[key] += 1
        self._counts = counts
        if crow_snapshot is not None:
            self._crow_snapshot = crow_snapshot
        self._update_text()

    def set_view(self, view: str) -> None:
        self._view = view
        self._update_text()

    def _theme_accent(self) -> str | None:
        try:
            app = self.app
        except Exception:
            return None
        theme = getattr(app, "current_theme", None)
        if theme is None:
            return None
        accent = getattr(theme, "accent", None)
        if not accent:
            return None
        return accent

    def _update_text(self) -> None:
        project = "[red][unconfigured][/red]" if self.project == "TODO_SET_ME" else self.project
        tabs = format_view_tabs(self._view, self._theme_accent())
        segments: list[str] = []
        if self._crow_snapshot is not None:
            inflight = format_inflight_segment(entries_from_snapshot(self._crow_snapshot))
            if inflight:
                segments.append(inflight)
        segments.extend(format_attention_segments(self._counts))
        suffix = f" · {' · '.join(segments)}" if segments else ""
        self.update(f"[b]murder[/b] · {project} · {tabs}{suffix}")
