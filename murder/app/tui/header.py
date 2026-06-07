"""Header bar: project identity, view tabs, in-flight crows, attention counts."""

from __future__ import annotations

import re

from textual.widgets import Static

from murder.app.service.client_api import CrowSnapshot, DispatchSnapshot, UsageGaugeSummary
from murder.app.tui.stores.roster import CrowEntry, _short_display_name, entries_from_snapshot
from murder.app.tui.dispatch.gauges import PROVIDER_ORDER, color_for_pct, fmt_duration

_ATTENTION_STATUSES = ("blocked", "failed")
_HEADER_CROW_ID_LIMIT = 3
_HEADER_NAME_MAX = 12
_VIEW_TABS = (
    ("planning", "planning"),
    ("crows", "crows"),
    ("schedule", "dispatch"),
)
_HARNESS_LABELS = {"claude_code": "claude"}
_RICH_TAG_RE = re.compile(r"\[/?[^\]]*\]")


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


def harness_display_label(harness: str) -> str:
    """Short provider label for the header usage segment."""
    return _HARNESS_LABELS.get(harness, harness)


def pick_soonest_per_harness(
    gauges: tuple[UsageGaugeSummary, ...],
) -> dict[str, UsageGaugeSummary]:
    """Per harness, keep the window with the smallest reset clock."""
    best: dict[str, UsageGaugeSummary] = {}
    for gauge in gauges:
        prev = best.get(gauge.harness)
        if prev is None or gauge.t_until_reset_minutes < prev.t_until_reset_minutes:
            best[gauge.harness] = gauge
    return best


def format_usage_segments(
    gauges: tuple[UsageGaugeSummary, ...],
    *,
    colorize: bool = True,
) -> list[str]:
    """Render '<harness> <pct>% <clock>' per provider, soonest reset per harness."""
    by_harness = pick_soonest_per_harness(gauges)
    if not by_harness:
        return []
    order = {harness: idx for idx, harness in enumerate(PROVIDER_ORDER)}
    harnesses = sorted(
        by_harness,
        key=lambda harness: (order.get(harness, len(order)), harness),
    )
    segments: list[str] = []
    for harness in harnesses:
        gauge = by_harness[harness]
        label = harness_display_label(harness)
        pct = round(gauge.pct)
        clock = fmt_duration(gauge.t_until_reset_minutes)
        if colorize:
            color = color_for_pct(gauge.pct)
            segments.append(f"{label} [{color}]{pct}%[/{color}] {clock}")
        else:
            segments.append(f"{label} {pct}% {clock}")
    return segments


def _visible_len(text: str) -> int:
    return len(_RICH_TAG_RE.sub("", text))


def compose_header_line(left: str, right: str, width: int | None) -> str:
    """Keep the identity/tabs group left and status segments right."""
    if not right:
        return left
    if width is None:
        return f"{left} · {right}"
    gap = width - _visible_len(left) - _visible_len(right)
    if gap < 1:
        return left
    return f"{left}{' ' * gap}{right}"


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
        self._usage_gauges: tuple[UsageGaugeSummary, ...] = ()

    def refresh_from_snapshot(
        self,
        snapshot: DispatchSnapshot,
        *,
        crow_snapshot: CrowSnapshot | None = None,
        usage_gauges: tuple[UsageGaugeSummary, ...] | None = None,
    ) -> None:
        counts = {s: 0 for s in _ATTENTION_STATUSES}
        for ticket in snapshot.tickets:
            key = ticket.status.value
            if key in counts:
                counts[key] += 1
        self._counts = counts
        if crow_snapshot is not None:
            self._crow_snapshot = crow_snapshot
        if usage_gauges is not None:
            self._usage_gauges = usage_gauges
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

    def _content_width(self) -> int | None:
        try:
            width = self.size.width
        except Exception:
            return None
        if width <= 0:
            return None
        # Static has horizontal padding 0 1 in DEFAULT_CSS.
        return max(0, width - 2)

    def _update_text(self) -> None:
        project = "[red][unconfigured][/red]" if self.project == "TODO_SET_ME" else self.project
        tabs = format_view_tabs(self._view, self._theme_accent())
        right_segments: list[str] = []
        if self._crow_snapshot is not None:
            inflight = format_inflight_segment(entries_from_snapshot(self._crow_snapshot))
            if inflight:
                right_segments.append(inflight)
        right_segments.extend(format_attention_segments(self._counts))
        usage_segments = format_usage_segments(self._usage_gauges)

        left = f"[b]murder[/b] · {project} · {tabs}"
        right = " · ".join([*right_segments, *usage_segments])

        width = self._content_width()
        line = compose_header_line(left, right, width)
        if line == left and usage_segments:
            right = " · ".join(right_segments)
            line = compose_header_line(left, right, width)

        self.update(line)
