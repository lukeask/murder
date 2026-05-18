"""Unit tests for dispatch mode picker behavior."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import murder.db as dbmod
from murder.tui.dispatch.mode_strip import ModeStrip, _crow_rationale


class _FakeModeStrip(ModeStrip):
    """Simple stand-in that avoids requiring a mounted Textual app."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []
        self.render_calls = 0

    def post_message(self, message):  # type: ignore[override]
        self.messages.append(message.to_mode)
        return True

    def _render_mode(self) -> None:  # type: ignore[override]
        self.render_calls += 1


def test_bindings_include_picker_controls() -> None:
    from collections import defaultdict

    keys_by_action: dict[str, set[str]] = defaultdict(set)
    for b in ModeStrip.BINDINGS:
        keys_by_action[b.action].add(b.key)
    assert "m" in keys_by_action["open_mode_picker"]
    assert "left" in keys_by_action["picker_left"]
    assert "h" in keys_by_action["picker_left"]
    assert "right" in keys_by_action["picker_right"]
    assert "l" in keys_by_action["picker_right"]
    assert "enter" in keys_by_action["picker_confirm"]
    assert "escape" in keys_by_action["picker_cancel"]


def test_open_picker_sets_index_to_current_mode() -> None:
    strip = _FakeModeStrip()
    strip._mode = "autorun_ready"
    strip.action_open_mode_picker()
    assert strip._picker_open is True
    assert strip._picker_index == 1


def test_left_and_right_wrap_when_picker_open() -> None:
    strip = _FakeModeStrip()
    strip.action_open_mode_picker()
    strip.action_picker_left()
    assert strip._picker_index == 2
    strip.action_picker_right()
    assert strip._picker_index == 0


def test_enter_confirms_selected_mode_and_closes_picker() -> None:
    strip = _FakeModeStrip()
    strip.action_open_mode_picker()
    strip.action_picker_right()
    strip.action_picker_confirm()
    assert strip._picker_open is False
    assert strip.messages == ["autorun_ready"]


def test_escape_cancels_picker_without_emitting_mode_change() -> None:
    strip = _FakeModeStrip()
    strip.action_open_mode_picker()
    strip.action_picker_right()
    strip.action_picker_cancel()
    assert strip._picker_open is False
    assert strip.messages == []


# ---------------------------------------------------------------------------
# _crow_rationale
# ---------------------------------------------------------------------------


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    dbmod.init_schema(conn)
    return conn


def _insert_decision(
    conn: sqlite3.Connection,
    harness: str = "cursor",
    window_key: str = "5h",
    decision: bool = False,
    rationale: str = "Holding: cursor/5h usage 30%",
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO scheduler_decision_cache
            (harness, window_key, mode, decision, usage, t_until_reset,
             t_period, threshold, rationale, kicked_ticket_id, updated_at)
        VALUES (?, ?, 'crow_magic', ?, 0.3, 200.0, 300.0, 0.45, ?, NULL, ?)
        """,
        (harness, window_key, int(decision), rationale, now),
    )


def test_crow_rationale_no_snapshots() -> None:
    db = _make_db()
    result = _crow_rationale(db)
    assert "ctrl+u" in result


def test_crow_rationale_snapshots_but_no_decisions() -> None:
    import json

    db = _make_db()
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO harness_usage_snapshots(harness, source, fetched_at, status_json) VALUES (?, ?, ?, ?)",
        ("cursor", "test", now, json.dumps({"windows": []})),
    )
    result = _crow_rationale(db)
    assert result == "evaluating…"


def test_crow_rationale_single_hold() -> None:
    db = _make_db()
    _insert_decision(db, "cursor", "5h", False, "Holding: cursor/5h usage 30%")
    result = _crow_rationale(db)
    assert result == "Holding: cursor/5h usage 30%"


def test_crow_rationale_multiple_holds() -> None:
    db = _make_db()
    _insert_decision(db, "cursor", "5h", False, "Holding: cursor/5h")
    _insert_decision(db, "codex", "30d", False, "Holding: codex/30d")
    result = _crow_rationale(db)
    assert "holding:" in result.lower()
    assert "cursor/5h" in result
    assert "codex/30d" in result


def test_crow_rationale_single_kick() -> None:
    db = _make_db()
    _insert_decision(db, "cursor", "5h", True, "Kicking t001: cursor/5h usage 85%")
    result = _crow_rationale(db)
    assert result == "Kicking t001: cursor/5h usage 85%"


def test_crow_rationale_kick_with_holds() -> None:
    db = _make_db()
    _insert_decision(db, "cursor", "5h", True, "Kicking t001: cursor/5h usage 85%")
    _insert_decision(db, "codex", "30d", False, "Holding: codex/30d")
    result = _crow_rationale(db)
    assert "1 holding" in result
    assert "Kicking t001" in result
