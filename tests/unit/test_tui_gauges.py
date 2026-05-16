"""Tests for GaugeStrip helper functions and rendering logic."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from murder.tui.dispatch.gauges import (
    GaugeStrip,
    _GaugeData,
    _color_for_pct,
    _fmt_duration,
    _load_gauges,
    _render_gauge,
    _ring_char,
)


# ---------------------------------------------------------------------------
# _ring_char
# ---------------------------------------------------------------------------

def test_ring_char_empty() -> None:
    assert _ring_char(0.0) == "○"


def test_ring_char_quarter() -> None:
    assert _ring_char(25.0) == "◔"


def test_ring_char_half() -> None:
    assert _ring_char(50.0) == "◑"


def test_ring_char_three_quarter() -> None:
    assert _ring_char(75.0) == "◕"


def test_ring_char_full() -> None:
    assert _ring_char(100.0) == "●"


def test_ring_char_just_below_boundary() -> None:
    assert _ring_char(24.9) == "○"
    assert _ring_char(49.9) == "◔"


# ---------------------------------------------------------------------------
# _fmt_duration
# ---------------------------------------------------------------------------

def test_fmt_duration_minutes() -> None:
    assert _fmt_duration(45.0) == "45m"


def test_fmt_duration_hours_no_minutes() -> None:
    assert _fmt_duration(120.0) == "2h"


def test_fmt_duration_hours_with_minutes() -> None:
    assert _fmt_duration(150.0) == "2h30m"


def test_fmt_duration_days() -> None:
    assert _fmt_duration(48 * 60 + 30) == "2d"


def test_fmt_duration_sub_minute() -> None:
    assert _fmt_duration(0.5) == "<1m"


# ---------------------------------------------------------------------------
# _color_for_pct
# ---------------------------------------------------------------------------

def test_color_green_below_60() -> None:
    assert _color_for_pct(59.9) == "green"


def test_color_yellow_60_to_80() -> None:
    assert _color_for_pct(60.0) == "yellow"
    assert _color_for_pct(79.9) == "yellow"


def test_color_red_80_and_above() -> None:
    assert _color_for_pct(80.0) == "red"
    assert _color_for_pct(100.0) == "red"


def test_color_red_on_decision_hold() -> None:
    assert _color_for_pct(10.0, decision_hold=True) == "red"
    assert _color_for_pct(50.0, decision_hold=True) == "red"


# ---------------------------------------------------------------------------
# _render_gauge
# ---------------------------------------------------------------------------

def test_render_gauge_focused_has_brackets() -> None:
    g = _GaugeData(harness="cursor", window_key="5h", pct=80.0, t_until_reset_minutes=120.0)
    rendered = _render_gauge(g, focused=True)
    # Focused gauge has brackets
    assert "[b][[/b]" in rendered


def test_render_gauge_unfocused_no_brackets() -> None:
    g = _GaugeData(harness="cursor", window_key="5h", pct=80.0, t_until_reset_minutes=120.0)
    rendered = _render_gauge(g, focused=False)
    assert "[b][[/b]" not in rendered


def test_render_gauge_contains_harness_and_window() -> None:
    g = _GaugeData(harness="cursor", window_key="5h", pct=45.0, t_until_reset_minutes=60.0)
    rendered = _render_gauge(g, focused=False)
    assert "cursor/5h" in rendered


def test_render_gauge_contains_pct() -> None:
    g = _GaugeData(harness="cursor", window_key="5h", pct=73.0, t_until_reset_minutes=30.0)
    rendered = _render_gauge(g, focused=False)
    assert "73%" in rendered


def test_render_gauge_contains_reset() -> None:
    g = _GaugeData(harness="cursor", window_key="5h", pct=50.0, t_until_reset_minutes=90.0)
    rendered = _render_gauge(g, focused=False)
    assert "rst" in rendered
    assert "1h30m" in rendered


def test_render_gauge_red_on_high_usage() -> None:
    g = _GaugeData(harness="cursor", window_key="5h", pct=85.0, t_until_reset_minutes=60.0)
    rendered = _render_gauge(g, focused=False)
    assert "[red]" in rendered


def test_render_gauge_green_on_low_usage() -> None:
    g = _GaugeData(harness="cursor", window_key="5h", pct=30.0, t_until_reset_minutes=60.0)
    rendered = _render_gauge(g, focused=False)
    assert "[green]" in rendered


def test_render_gauge_red_on_decision_hold() -> None:
    g = _GaugeData(
        harness="cursor", window_key="5h", pct=20.0,
        t_until_reset_minutes=60.0, decision_hold=True,
    )
    rendered = _render_gauge(g, focused=False)
    assert "[red]" in rendered


# ---------------------------------------------------------------------------
# _load_gauges
# ---------------------------------------------------------------------------

def _make_db() -> sqlite3.Connection:
    import murder.db as dbmod
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    dbmod.init_schema(conn)
    return conn


def _insert_snapshot(
    conn: sqlite3.Connection,
    harness: str,
    pct: float,
    window_key: str = "current_period",
    t_until_minutes: float = 120.0,
    fetched_at: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    reset_at = now + timedelta(minutes=t_until_minutes)
    window = {
        "name": window_key,
        "percent_used": pct,
        "reset_at": reset_at.isoformat(),
    }
    payload = {
        "harness": harness,
        "source": "test",
        "fetched_at": fetched_at or now.isoformat(),
        "windows": [window],
    }
    conn.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) VALUES (?, ?, ?, ?)",
        (harness, "test", fetched_at or now.isoformat(), json.dumps(payload)),
    )


def test_load_gauges_returns_gauge_per_window(tmp_path: pytest.fixture) -> None:
    db = _make_db()
    _insert_snapshot(db, "cursor", 50.0, "5h")
    _insert_snapshot(db, "codex", 70.0, "current_period")

    gauges = _load_gauges(db)
    harnesses = {g.harness for g in gauges}
    assert "cursor" in harnesses
    assert "codex" in harnesses


def test_load_gauges_empty_when_no_snapshots() -> None:
    db = _make_db()
    gauges = _load_gauges(db)
    assert gauges == []


def test_load_gauges_marks_decision_hold() -> None:
    db = _make_db()
    now = datetime.now(timezone.utc)
    _insert_snapshot(db, "cursor", 10.0, "5h")
    db.execute(
        """
        INSERT INTO scheduler_decision_cache
            (harness, window_key, mode, decision, usage, t_until_reset,
             t_period, threshold, rationale, kicked_ticket_id, updated_at)
        VALUES ('cursor', '5h', 'crow_magic', 0, 0.10, 200.0, 300.0, 0.30,
                'Holding', NULL, ?)
        """,
        (now.isoformat(),),
    )
    gauges = _load_gauges(db)
    cursor_gauges = [g for g in gauges if g.harness == "cursor"]
    assert len(cursor_gauges) == 1
    assert cursor_gauges[0].decision_hold is True


def test_load_gauges_no_hold_when_decision_kick() -> None:
    db = _make_db()
    now = datetime.now(timezone.utc)
    _insert_snapshot(db, "cursor", 85.0, "5h")
    db.execute(
        """
        INSERT INTO scheduler_decision_cache
            (harness, window_key, mode, decision, usage, t_until_reset,
             t_period, threshold, rationale, kicked_ticket_id, updated_at)
        VALUES ('cursor', '5h', 'crow_magic', 1, 0.85, 200.0, 300.0, 0.60,
                'Kicking t001', 't001', ?)
        """,
        (now.isoformat(),),
    )
    gauges = _load_gauges(db)
    cursor_gauges = [g for g in gauges if g.harness == "cursor"]
    assert cursor_gauges[0].decision_hold is False


# ---------------------------------------------------------------------------
# GaugeStrip.refresh_from_db + navigation
# ---------------------------------------------------------------------------

def test_gauge_strip_empty_db_renders_empty() -> None:
    db = _make_db()
    strip = GaugeStrip()
    strip.refresh_from_db(db)
    # No crash and no gauges
    assert strip._gauges == []


def test_gauge_strip_refresh_populates_gauges() -> None:
    db = _make_db()
    _insert_snapshot(db, "cursor", 50.0)
    _insert_snapshot(db, "codex", 70.0)

    strip = GaugeStrip()
    strip.refresh_from_db(db)
    assert len(strip._gauges) == 2


def test_gauge_strip_focus_cycles() -> None:
    db = _make_db()
    _insert_snapshot(db, "cursor", 50.0)
    _insert_snapshot(db, "codex", 70.0)

    strip = GaugeStrip()
    strip.refresh_from_db(db)

    assert strip._focus_idx == 0
    strip.action_focus_next()
    assert strip._focus_idx == 1
    strip.action_focus_next()
    # Wraps back to 0
    assert strip._focus_idx == 0


def test_gauge_strip_focus_prev_wraps() -> None:
    db = _make_db()
    _insert_snapshot(db, "cursor", 50.0)
    _insert_snapshot(db, "codex", 70.0)

    strip = GaugeStrip()
    strip.refresh_from_db(db)

    strip.action_focus_prev()
    assert strip._focus_idx == 1


def test_gauge_strip_focus_clamped_on_refresh() -> None:
    db = _make_db()
    _insert_snapshot(db, "cursor", 50.0)
    _insert_snapshot(db, "codex", 70.0)

    strip = GaugeStrip()
    strip.refresh_from_db(db)
    strip._focus_idx = 5

    # Now refresh with only 1 gauge
    db2 = _make_db()
    _insert_snapshot(db2, "cursor", 50.0)
    strip.refresh_from_db(db2)
    assert strip._focus_idx == 0
