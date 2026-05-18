"""Usage gauge strip — per-harness usage gauges with color-coded rings."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

# Unicode ring chars: empty → quarter → half → three-quarter → full
_RING = ["○", "◔", "◑", "◕", "●"]
# Sparkline bar characters (1/8 to 8/8 fill)
_SPARK_BARS = "▁▂▃▄▅▆▇█"

_WINDOW_NAME_RE = re.compile(r"^(\d+)(h|d)$", re.IGNORECASE)
_WINDOW_NAME_MINUTES = {"h": 60.0, "d": 1440.0}

# Usage-color thresholds (percent used). Lower usage is greener — unused quota
# is safe headroom, high usage is the danger zone. Tune these to shift bands.
_USAGE_YELLOW_PCT = 40.0  # >= this: "moderate usage"
_USAGE_RED_PCT = 75.0  # >= this: "high usage" — running low on quota


def _ring_char(pct: float) -> str:
    """Map 0-100 percent to a Unicode ring fill character."""
    idx = min(int(pct / 25), 4)
    return _RING[idx]


def _fmt_duration(minutes: float) -> str:
    """Format a duration in minutes as a short human-readable string."""
    if minutes < 1:
        return "<1m"
    if minutes < 60:
        return f"{int(minutes)}m"
    hours = int(minutes // 60)
    mins = int(minutes % 60)
    if hours < 48:
        return f"{hours}h{mins:02d}m" if mins else f"{hours}h"
    days = hours // 24
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


def _pct_from_window(payload: dict[str, Any], window_key: str) -> float | None:
    """Return percent_used for the named window from a snapshot payload."""
    for w in payload.get("windows") or []:
        if not isinstance(w, dict):
            continue
        if (w.get("name") or "usage") == window_key:
            pct = w.get("percent_used")
            if isinstance(pct, (int, float)):
                return float(pct)
    return None


@dataclass
class _GaugeData:
    harness: str
    window_key: str
    pct: float
    t_until_reset_minutes: float
    t_period_minutes: float = 0.0


def _load_gauges(db: sqlite3.Connection) -> list[_GaugeData]:
    """Read latest harness snapshots and decision cache; build gauge list."""
    snap_rows = db.execute(
        """
        SELECT s.harness, s.status_json
          FROM harness_usage_snapshots s
          JOIN (
                SELECT harness, MAX(fetched_at) AS fetched_at
                  FROM harness_usage_snapshots
                 GROUP BY harness
               ) latest
            ON latest.harness = s.harness
           AND latest.fetched_at = s.fetched_at
         ORDER BY s.harness
        """
    ).fetchall()

    now = datetime.now(timezone.utc)
    gauges: list[_GaugeData] = []

    for snap_row in snap_rows:
        harness = snap_row["harness"]
        try:
            payload = json.loads(snap_row["status_json"])
        except (TypeError, ValueError):
            continue

        windows = payload.get("windows") or []
        for window in windows:
            if not isinstance(window, dict):
                continue
            pct = window.get("percent_used")
            if not isinstance(pct, (int, float)):
                continue
            window_key = window.get("name") or "usage"
            reset_at_str = window.get("reset_at") or window.get("ends_at")
            t_until = 0.0
            if reset_at_str:
                try:
                    reset_at = datetime.fromisoformat(reset_at_str)
                    if reset_at.tzinfo is None:
                        reset_at = reset_at.replace(tzinfo=timezone.utc)
                    t_until = max(0.0, (reset_at - now).total_seconds() / 60.0)
                except (ValueError, TypeError):
                    pass
            t_period = _period_minutes_for(harness, window_key, window)
            gauges.append(
                _GaugeData(
                    harness=harness,
                    window_key=window_key,
                    pct=float(pct),
                    t_until_reset_minutes=t_until,
                    t_period_minutes=t_period,
                )
            )

    # Column-major order: group by provider so same-harness windows are
    # adjacent. Stable sort preserves per-harness window order.
    order = {h: i for i, h in enumerate(_PROVIDER_ORDER)}
    gauges.sort(key=lambda g: order.get(g.harness, len(order)))

    return gauges


# Providers are laid out as side-by-side columns in this order; unlisted
# harnesses follow, in load order. _PROVIDER_LABELS overrides the column header.
_PROVIDER_ORDER = ("claude_code", "codex", "cursor")
_PROVIDER_LABELS = {"claude_code": "claude code"}


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
# Drill-in helpers
# ---------------------------------------------------------------------------


def _spark_history(db: sqlite3.Connection, harness: str, window_key: str) -> str:
    """Return a 14-char block sparkline of daily avg usage for a harness/window."""
    rows = db.execute(
        """
        SELECT date(fetched_at) AS day, status_json
          FROM harness_usage_snapshots
         WHERE harness = ?
           AND fetched_at >= datetime('now', '-14 days')
         ORDER BY fetched_at
        """,
        (harness,),
    ).fetchall()

    by_day: dict[str, list[float]] = {}
    for row in rows:
        day = row["day"]
        try:
            payload = json.loads(row["status_json"])
        except (TypeError, ValueError):
            continue
        pct = _pct_from_window(payload, window_key)
        if pct is not None:
            by_day.setdefault(day, []).append(pct)

    if not by_day:
        return "(no history)"

    today = datetime.now(timezone.utc).date()
    spark = []
    for i in range(13, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        pcts = by_day.get(d, [])
        avg = sum(pcts) / len(pcts) if pcts else 0.0
        idx = min(int(avg / 100.0 * 8), 7)
        spark.append(_SPARK_BARS[idx])
    return "".join(spark)


def _recent_reset_events(
    db: sqlite3.Connection, harness: str, window_key: str, days: int = 14
) -> list[dict[str, Any]]:
    """Scan last `days` days of snapshots for usage-reset pairs."""
    rows = db.execute(
        """
        SELECT fetched_at, status_json FROM harness_usage_snapshots
         WHERE harness = ?
           AND fetched_at >= datetime('now', '-' || ? || ' days')
         ORDER BY fetched_at ASC
        """,
        (harness, days),
    ).fetchall()

    resets: list[dict[str, Any]] = []
    for i in range(1, len(rows)):
        try:
            curr = json.loads(rows[i]["status_json"])
            prev = json.loads(rows[i - 1]["status_json"])
        except (TypeError, ValueError):
            continue
        curr_pct = _pct_from_window(curr, window_key)
        prev_pct = _pct_from_window(prev, window_key)
        if curr_pct is None or prev_pct is None:
            continue
        if prev_pct >= 30.0 and curr_pct <= 5.0:
            resets.append({"reset_at": rows[i]["fetched_at"], "peak_pct": prev_pct})
    return resets


_BURN_SQL = """
SELECT
    t.id,
    t.title,
    CAST(
        (JULIANDAY(COALESCE(a.last_heartbeat_at, ?))
         - JULIANDAY(
             CASE WHEN a.started_at > ? THEN a.started_at ELSE ? END
         )) * 1440 AS INTEGER
    ) AS active_minutes
  FROM agents a
  JOIN tickets t ON t.id = a.ticket_id
 WHERE t.harness = ?
   AND COALESCE(a.last_heartbeat_at, ?) > ?
GROUP BY t.id, t.title
HAVING active_minutes > 0
ORDER BY active_minutes DESC
LIMIT 10
"""


def _burn_attribution(
    db: sqlite3.Connection, harness: str, t_period_minutes: float
) -> list[dict[str, Any]]:
    """Return top tickets by active time during the billing window."""
    if t_period_minutes <= 0:
        return []
    now = datetime.now(timezone.utc)
    window_start = (now - timedelta(minutes=t_period_minutes)).isoformat()
    now_str = now.isoformat()
    rows = db.execute(
        _BURN_SQL,
        (now_str, window_start, window_start, harness, now_str, window_start),
    ).fetchall()
    return [
        {
            "ticket_id": r["id"],
            "title": r["title"],
            "active_minutes": int(r["active_minutes"]),
        }
        for r in rows
    ]


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

    def __init__(self, gauge: _GaugeData, db: sqlite3.Connection) -> None:
        super().__init__()
        self._gauge = gauge
        self._db = db

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

        # 14-day sparkline
        spark = _spark_history(self._db, g.harness, g.window_key)
        lines += [f"[b]14-day history[/b]  {spark}", ""]

        # Recent resets
        resets = _recent_reset_events(self._db, g.harness, g.window_key)
        lines.append("[b]Recent resets[/b]")
        if resets:
            for r in resets:
                day = r["reset_at"][:10]
                lines.append(f"  Hit {r['peak_pct']:.0f}% before reset on {day}")
        else:
            lines.append("  (none in last 14 days)")
        lines.append("")

        # Burn attribution
        burn = _burn_attribution(self._db, g.harness, g.t_period_minutes)
        lines.append("[b]What burned this period[/b]")
        if burn:
            for b in burn:
                dur = _fmt_duration(float(b["active_minutes"]))
                lines.append(f"  {b['ticket_id']} · {b['title']} ({dur})")
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
            from murder.tui.dispatch.mode_strip import ModeStrip

            self.app.query_one(ModeStrip).action_open_mode_picker()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# GaugeStrip widget
# ---------------------------------------------------------------------------


class GaugeStrip(Static):
    """Per-provider usage gauges, one column per harness, windows stacked."""

    BINDINGS = [
        Binding("left", "focus_prev", "Prev gauge", show=False),
        Binding("right", "focus_next", "Next gauge", show=False),
        Binding("enter", "drill_in", "Gauge detail", show=False),
    ]

    DEFAULT_CSS = """
    GaugeStrip {
        height: auto;
        color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__("")
        self._gauges: list[_GaugeData] = []
        self._focus_idx: int = 0
        self._db: sqlite3.Connection | None = None

    def refresh_from_db(self, db: sqlite3.Connection | None) -> None:
        if db is None:
            return
        self._db = db
        self._gauges = _load_gauges(db)
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
        if not self._gauges or self._db is None:
            return
        gauge = self._gauges[self._focus_idx]
        self.app.push_screen(GaugeDrillIn(gauge, self._db))
