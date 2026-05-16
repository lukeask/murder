"""Usage gauge strip — per-harness usage gauges with color-coded rings."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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


def _color_for_pct(pct: float, decision_hold: bool = False) -> str:
    """Return a Rich color name for the given usage percentage."""
    if decision_hold:
        return "red"
    if pct >= 80.0:
        return "red"
    if pct >= 60.0:
        return "yellow"
    return "green"


def _t_period_from_window(window: dict[str, Any], window_key: str) -> float:
    """Derive t_period in minutes from a window dict, falling back to the window name."""
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
        value = float(m.group(1))
        unit = m.group(2).lower()
        return value * _WINDOW_NAME_MINUTES[unit]
    return 0.0


def _pct_from_window(payload: dict[str, Any], window_key: str) -> float | None:
    """Return percent_used for the named window from a snapshot payload."""
    for w in (payload.get("windows") or []):
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
    decision_hold: bool = False
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
            t_period = _t_period_from_window(window, window_key)
            gauges.append(_GaugeData(
                harness=harness,
                window_key=window_key,
                pct=float(pct),
                t_until_reset_minutes=t_until,
                t_period_minutes=t_period,
            ))

    # Overlay decision cache to mark holds
    try:
        dec_rows = db.execute(
            "SELECT harness, window_key, decision FROM scheduler_decision_cache"
        ).fetchall()
        dec_lookup = {
            (r["harness"], r["window_key"]): bool(r["decision"])
            for r in dec_rows
        }
        for g in gauges:
            decision = dec_lookup.get((g.harness, g.window_key))
            if decision is False:
                g.decision_hold = True
    except Exception:
        pass

    return gauges


def _render_gauge(g: _GaugeData, focused: bool) -> str:
    """Render a single gauge as a Rich markup string."""
    color = _color_for_pct(g.pct, g.decision_hold)
    ring = _ring_char(g.pct)
    label = f"{g.harness}/{g.window_key}"
    rst = _fmt_duration(g.t_until_reset_minutes)
    body = f"[{color}]{ring}[/{color}] {label} {g.pct:.0f}%  rst {rst}"
    if focused:
        return f"[b][[/b]{body}[b]][/b]"
    return f" {body} "


# ---------------------------------------------------------------------------
# Drill-in helpers
# ---------------------------------------------------------------------------

def _spark_history(
    db: sqlite3.Connection, harness: str, window_key: str
) -> str:
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
        color = _color_for_pct(g.pct, g.decision_hold)
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
    """Horizontal row of per-(harness, window) usage gauges."""

    BINDINGS = [
        Binding("left", "focus_prev", "Prev gauge", show=False),
        Binding("right", "focus_next", "Next gauge", show=False),
        Binding("enter", "drill_in", "Gauge detail", show=False),
    ]

    DEFAULT_CSS = """
    GaugeStrip {
        height: 1;
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
        if not self._gauges:
            self.update("")
            return
        parts = [
            _render_gauge(g, i == self._focus_idx)
            for i, g in enumerate(self._gauges)
        ]
        self.update("  ".join(parts))

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
