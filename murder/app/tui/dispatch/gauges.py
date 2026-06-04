"""Usage gauge strip — per-harness usage gauges with color-coded rings."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from murder.app.service.client_api import (
    ScheduleSnapshot,
    UsageGaugeDrillInSnapshot,
    UsageGaugeSummary,
)
from murder.app.tui.dispatch.mode_strip import ModeStrip

UsageDrillInLoader = Callable[..., Awaitable[UsageGaugeDrillInSnapshot]]

# Unicode ring chars: empty → quarter → half → three-quarter → full
_RING = ["○", "◔", "◑", "◕", "●"]
_WINDOW_NAME_RE = re.compile(r"^(\d+)(h|d)$", re.IGNORECASE)
_WINDOW_NAME_MINUTES = {"h": 60.0, "d": 1440.0}

# Usage-color thresholds (percent used). Lower usage is greener — unused quota
# is safe headroom, high usage is the danger zone. Tune these to shift bands.
_USAGE_YELLOW_PCT = 40.0  # >= this: "moderate usage"
_USAGE_RED_PCT = 75.0  # >= this: "high usage" — running low on quota
_MINUTES_PER_HOUR = 60
_HOURS_PER_TWO_DAYS = 48
_HOURS_PER_DAY = 24


def _ring_char(pct: float) -> str:
    """Map 0-100 percent to a Unicode ring fill character."""
    idx = min(int(pct / 25), 4)
    return _RING[idx]


def _fmt_duration(minutes: float) -> str:
    """Format a duration in minutes as a short human-readable string."""
    if minutes < 1:
        return "<1m"
    if minutes < _MINUTES_PER_HOUR:
        return f"{int(minutes)}m"
    hours = int(minutes // _MINUTES_PER_HOUR)
    mins = int(minutes % _MINUTES_PER_HOUR)
    if hours < _HOURS_PER_TWO_DAYS:
        return f"{hours}h{mins:02d}m" if mins else f"{hours}h"
    days = hours // _HOURS_PER_DAY
    return f"{days}d"


def _color_for_pct(pct: float) -> str:
    """Return a Rich color name for a usage percentage.

    Color reflects the meter itself — purely the percentage consumed. Lower
    usage is greener; high usage is the danger zone. Scheduler state (holds)
    deliberately does not affect gauge color.
    """
    if pct >= _USAGE_RED_PCT:
        return "red"
    if pct >= _USAGE_YELLOW_PCT:
        return "yellow"
    return "green"


# Billing-period lengths, in minutes. Fixed defaults until each provider can
# report its real cycle length; `_period_minutes_for()` is the single seam to
# swap for auto-detection later — callers never touch this table directly.
_CLAUDE_CODE_SESSION_MINUTES = 5 * 60.0
_CODEX_5H_MINUTES = 5 * 60.0
_CODEX_WEEKLY_MINUTES = 7 * 24 * 60.0
_CURSOR_PERIOD_MINUTES = 30 * 24 * 60.0

_PERIOD_MINUTES: dict[tuple[str, str], float] = {
    ("claude_code", "current_session"): _CLAUDE_CODE_SESSION_MINUTES,
    ("codex", "5h"): _CODEX_5H_MINUTES,
    ("codex", "weekly"): _CODEX_WEEKLY_MINUTES,
    ("cursor", "auto_composer"): _CURSOR_PERIOD_MINUTES,
    ("cursor", "api"): _CURSOR_PERIOD_MINUTES,
}


def _period_minutes_for(harness: str, window_key: str, window: dict[str, Any]) -> float:
    """Billing-period length in minutes for a usage window.

    Resolution order: explicit ``starts_at``/``ends_at`` on the snapshot, then
    an ``<n>h``/``<n>d`` window name, then the static ``_PERIOD_MINUTES`` table.
    Replace this body when period auto-detection lands. Returns 0.0 if unknown.
    """
    starts_at_str = window.get("starts_at")
    end_str = window.get("ends_at") or window.get("reset_at")
    if starts_at_str and end_str:
        try:
            sa = datetime.fromisoformat(starts_at_str)
            ea = datetime.fromisoformat(end_str)
            period_m = (ea - sa).total_seconds() / 60.0
            if period_m > 0:
                return period_m
        except (ValueError, TypeError):
            pass
    m = _WINDOW_NAME_RE.match(window_key)
    if m:
        return float(m.group(1)) * _WINDOW_NAME_MINUTES[m.group(2).lower()]
    return _PERIOD_MINUTES.get((harness, window_key), 0.0)


@dataclass
class _GaugeData:
    harness: str
    window_key: str
    pct: float
    t_until_reset_minutes: float
    t_period_minutes: float = 0.0


def _gauge_from_summary(summary: UsageGaugeSummary) -> _GaugeData:
    return _GaugeData(
        harness=summary.harness,
        window_key=summary.window_key,
        pct=summary.pct,
        t_until_reset_minutes=summary.t_until_reset_minutes,
        t_period_minutes=summary.t_period_minutes,
    )


# Providers are laid out as side-by-side columns in this order; unlisted
# harnesses follow, in load order. _PROVIDER_LABELS overrides the column header.
PROVIDER_ORDER = ("claude_code", "codex", "cursor")
_PROVIDER_ORDER = PROVIDER_ORDER
_PROVIDER_LABELS = {"claude_code": "claude code"}

# Shared with header bar usage segment (same thresholds as gauge cells).
fmt_duration = _fmt_duration
color_for_pct = _color_for_pct


def _gauge_text(g: _GaugeData) -> str:
    """Plain (markup-free) gauge cell text, sans focus brackets."""
    ring = _ring_char(g.pct)
    rst = _fmt_duration(g.t_until_reset_minutes)
    period = _fmt_duration(g.t_period_minutes) if g.t_period_minutes > 0 else "?"
    return f"{ring} {g.window_key} {g.pct:.0f}% rst {rst}/{period}"


def _gauge_width(g: _GaugeData) -> int:
    """Visible width of a rendered gauge cell (text plus focus brackets/pad)."""
    return len(_gauge_text(g)) + 2


def _render_gauge(g: _GaugeData, focused: bool) -> str:
    """Render a single gauge as a Rich markup string.

    The harness is conveyed by the column header, so the cell shows only the
    window: ring, window key, percent used, and `reset/period` durations.
    """
    color = _color_for_pct(g.pct)
    ring = _ring_char(g.pct)
    rst = _fmt_duration(g.t_until_reset_minutes)
    period = _fmt_duration(g.t_period_minutes) if g.t_period_minutes > 0 else "?"
    body = f"[{color}]{ring}[/{color}] {g.window_key} {g.pct:.0f}% rst {rst}/{period}"
    if focused:
        return f"[b]\\[[/b]{body}[b]][/b]"
    return f" {body} "


def _build_strip_text(gauges: list[_GaugeData], focus_idx: int) -> str:
    """Lay out gauges as side-by-side provider columns, windows stacked.

    `gauges` is provider-ordered by `_load_gauges`, so each maximal run of one
    harness becomes a column: a bold header plus one gauge cell per window.
    """
    if not gauges:
        return ""

    columns: list[dict[str, Any]] = []
    for idx, g in enumerate(gauges):
        if columns and columns[-1]["harness"] == g.harness:
            columns[-1]["cells"].append((idx, g))
        else:
            columns.append({"harness": g.harness, "cells": [(idx, g)]})

    for col in columns:
        col["header"] = _PROVIDER_LABELS.get(col["harness"], col["harness"])
        col["width"] = max([len(col["header"])] + [_gauge_width(g) for _, g in col["cells"]])

    gap = "  "
    n_rows = max(len(col["cells"]) for col in columns)

    lines = [
        gap.join(
            f"[b]{col['header']}[/b]" + " " * (col["width"] - len(col["header"])) for col in columns
        )
    ]
    for row in range(n_rows):
        cells = []
        for col in columns:
            if row < len(col["cells"]):
                idx, g = col["cells"][row]
                cell = _render_gauge(g, idx == focus_idx)
                cells.append(cell + " " * (col["width"] - _gauge_width(g)))
            else:
                cells.append(" " * col["width"])
        lines.append(gap.join(cells))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GaugeDrillIn modal
# ---------------------------------------------------------------------------


class GaugeDrillIn(ModalScreen[None]):
    """Full-screen detail view for a single (harness, window) usage gauge."""

    BINDINGS = [
        Binding("escape", "dismiss", "Back"),
        Binding("u", "cycle_provider", "Provider", show=False),
        Binding("U", "cycle_provider", "Provider", show=False),
        Binding("m", "open_mode_picker", "Mode", show=False),
    ]

    CSS = """
    GaugeDrillIn {
        align: center middle;
    }
    #drill_box {
        width: 80;
        max-width: 98%;
        max-height: 90%;
        border: solid $primary;
        background: $surface;
        padding: 0;
    }
    #drill_title {
        background: $primary;
        color: $background;
        text-align: center;
        height: 1;
        padding: 0 2;
        text-style: bold;
    }
    #drill_scroll {
        height: 1fr;
        padding: 1 2;
    }
    #drill_help {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, gauge: _GaugeData, drill_in: UsageGaugeDrillInSnapshot) -> None:
        super().__init__()
        self._gauge = gauge
        self._drill_in = drill_in

    def compose(self) -> ComposeResult:
        g = self._gauge
        yield Vertical(
            Static(
                f"{g.harness} / {g.window_key}",
                id="drill_title",
            ),
            ScrollableContainer(
                Static(self._build_content(), id="drill_body"),
                id="drill_scroll",
            ),
            Static(
                "[esc] back  [m] mode  [u/U] provider",
                id="drill_help",
            ),
            id="drill_box",
        )

    def _build_content(self) -> str:
        g = self._gauge
        color = _color_for_pct(g.pct)
        ring = _ring_char(g.pct)
        rst = _fmt_duration(g.t_until_reset_minutes)

        lines: list[str] = [
            f"[{color}]{ring}[/{color}]  [{color}]{g.pct:.0f}%[/{color}]  rst {rst}",
            "",
        ]

        drill = self._drill_in
        lines += [f"[b]14-day history[/b]  {drill.sparkline}", ""]

        lines.append("[b]Recent resets[/b]")
        if drill.recent_resets:
            for r in drill.recent_resets:
                day = r.reset_at[:10]
                lines.append(f"  Hit {r.peak_pct:.0f}% before reset on {day}")
        else:
            lines.append("  (none in last 14 days)")
        lines.append("")

        lines.append("[b]What burned this period[/b]")
        if drill.burn_rows:
            for b in drill.burn_rows:
                dur = _fmt_duration(float(b.active_minutes))
                lines.append(f"  {b.ticket_id} · {b.title} ({dur})")
        else:
            lines.append("  (no active tickets this period)")

        return "\n".join(lines)

    def action_dismiss(self) -> None:
        self.dismiss()

    def action_cycle_provider(self) -> None:
        pass  # placeholder — provider cycling not yet implemented

    def action_open_mode_picker(self) -> None:
        self.dismiss()
        try:
            self.app.query_one(ModeStrip).action_open_mode_picker()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# GaugeStrip widget
# ---------------------------------------------------------------------------


class GaugeStrip(Static):
    """Per-provider usage gauges, one column per harness, windows stacked."""

    can_focus = True

    BINDINGS = [
        Binding("left", "focus_prev", "Prev gauge", show=False),
        Binding("right", "focus_next", "Next gauge", show=False),
        Binding("enter", "drill_in", "Gauge detail", show=False),
    ]

    DEFAULT_CSS = """
    GaugeStrip {
        height: auto;
        color: $text-muted;
        border: solid $border;
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self._gauges: list[_GaugeData] = []
        self._focus_idx: int = 0
        self._drill_in_loader: UsageDrillInLoader | None = None

    def set_drill_in_loader(self, loader: UsageDrillInLoader) -> None:
        self._drill_in_loader = loader

    def refresh_from_snapshot(self, snapshot: ScheduleSnapshot) -> None:
        self._gauges = [_gauge_from_summary(g) for g in snapshot.usage_gauges]
        if self._focus_idx >= len(self._gauges):
            self._focus_idx = max(0, len(self._gauges) - 1)
        self._render_strip()

    def _render_strip(self) -> None:
        self.update(_build_strip_text(self._gauges, self._focus_idx))

    def action_focus_prev(self) -> None:
        if self._gauges:
            self._focus_idx = (self._focus_idx - 1) % len(self._gauges)
            self._render_strip()

    def action_focus_next(self) -> None:
        if self._gauges:
            self._focus_idx = (self._focus_idx + 1) % len(self._gauges)
            self._render_strip()

    def action_drill_in(self) -> None:
        if not self._gauges or self._drill_in_loader is None:
            return
        gauge = self._gauges[self._focus_idx]
        self.app.run_worker(
            self._open_drill_in(gauge),
            exclusive=True,
            group="usage_drill_in",
        )

    async def _open_drill_in(self, gauge: _GaugeData) -> None:
        if self._drill_in_loader is None:
            return
        drill_in = await self._drill_in_loader(
            harness=gauge.harness,
            window_key=gauge.window_key,
            t_period_minutes=gauge.t_period_minutes,
        )
        self.app.push_screen(GaugeDrillIn(gauge, drill_in))
