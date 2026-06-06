from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from murder.llm.harnesses.usage import parse_claude_usage_pane, parse_codex_status_pane


def _load_pane_fixture(name: str) -> str:
    fixture = Path(f"tests/fixtures/harness_panes/{name}").read_text()
    return "\n".join(line for line in fixture.splitlines() if not line.startswith("# source"))


def _codex_session_limit_pane() -> str:
    return _load_pane_fixture("codex_session_limit.txt")


def test_claude_usage_parses_bare_hour_reset() -> None:
    # Claude's /usage renders top-of-hour resets without minutes, e.g. `12am`.
    pane = (
        "Current session\n"
        "[====]  15% used\n"
        "Resets 12am (America/New_York)\n"
    )
    now = datetime(2026, 6, 1, 23, 40, tzinfo=ZoneInfo("America/New_York"))
    status = parse_claude_usage_pane(pane, now=now)
    assert status.windows[0].percent_used == 15.0
    reset_at = datetime.fromisoformat(status.windows[0].reset_at)
    assert reset_at.hour == 0 and reset_at.minute == 0
    t_until_minutes = (reset_at - now).total_seconds() / 60.0
    assert t_until_minutes == 20.0


def test_codex_5h_limit_parses_left_as_used() -> None:
    status = parse_codex_status_pane(_codex_session_limit_pane())
    by_name = {w.name: w for w in status.windows}
    assert by_name["5h"].percent_used == 100.0
    assert by_name["weekly"].percent_used == 57.0


def test_codex_reset_clock_uses_local_timezone_not_utc() -> None:
    eastern = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 27, 19, 58, tzinfo=eastern)
    status = parse_codex_status_pane(_codex_session_limit_pane(), now=now)
    reset_at = datetime.fromisoformat(status.windows[0].reset_at)
    assert reset_at.tzinfo is not None
    assert reset_at.date() == now.date()
    assert reset_at.hour == 20 and reset_at.minute == 43
    t_until_minutes = (reset_at - now).total_seconds() / 60.0
    assert 40.0 < t_until_minutes < 50.0


def test_claude_usage_uses_latest_reset_when_scrollback_has_stale_entry() -> None:
    # Scrollback has an old /usage overlay with "Resets 11pm"; the current
    # overlay below it shows "Resets 1am". Parser must return 1am, not 11pm.
    pane = _load_pane_fixture("cc_usage_scrollback.txt")
    # now = 11:30pm Eastern — 11pm has already passed, 1am is 1.5h away
    now = datetime(2026, 6, 1, 23, 30, tzinfo=ZoneInfo("America/New_York"))
    status = parse_claude_usage_pane(pane, now=now)
    assert status.windows[0].percent_used == 15.0
    reset_at = datetime.fromisoformat(status.windows[0].reset_at)
    assert reset_at.hour == 1 and reset_at.minute == 0
    t_until_minutes = (reset_at - now).total_seconds() / 60.0
    assert 85.0 < t_until_minutes < 95.0  # ~90 min, not ~23h


def test_codex_status_uses_latest_scrollback_row_per_window() -> None:
    eastern = ZoneInfo("America/New_York")
    now = datetime(2026, 5, 27, 21, 14, tzinfo=eastern)
    status = parse_codex_status_pane(_load_pane_fixture("codex_status_scrollback.txt"), now=now)
    by_name = {w.name: w for w in status.windows}
    assert by_name["5h"].percent_used == 1.0
    reset_at = datetime.fromisoformat(by_name["5h"].reset_at)
    assert reset_at.month == 5 and reset_at.day == 28
    assert reset_at.hour == 2 and reset_at.minute == 14
    t_until_minutes = (reset_at - now).total_seconds() / 60.0
    assert 4.5 * 60 < t_until_minutes < 5.5 * 60


def test_claude_usage_dialog_narrow_parses_block_bar_and_reset() -> None:
    # Real /usage overlay captured at ~93 cols; block-char bar, "1:40pm (America/New_York)"
    pane = _load_pane_fixture("cc_usage_dialog_narrow.txt")
    now = datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    status = parse_claude_usage_pane(pane, now=now)
    assert len(status.windows) == 1
    assert status.windows[0].percent_used == 84.0
    reset_at = datetime.fromisoformat(status.windows[0].reset_at)
    assert reset_at.hour == 13 and reset_at.minute == 40
    t_until_minutes = (reset_at - now).total_seconds() / 60.0
    assert 3.5 * 60 < t_until_minutes < 4.0 * 60  # ~3h40m away


def test_claude_usage_dialog_weekly_parses_both_windows() -> None:
    # Max plan: /usage shows both "Current session" and "Current week (all models)".
    # Reset for weekly uses "Jun 13, 2am" format (month + day + bare hour).
    pane = _load_pane_fixture("cc_usage_dialog_weekly.txt")
    now = datetime(2026, 6, 6, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    status = parse_claude_usage_pane(pane, now=now)
    assert len(status.windows) == 2
    by_name = {w.name: w for w in status.windows}
    assert by_name["current_session"].percent_used == 2.0
    session_reset = datetime.fromisoformat(by_name["current_session"].reset_at)
    assert session_reset.hour == 15 and session_reset.minute == 30  # 3:30pm
    assert by_name["current_week"].percent_used == 0.0
    week_reset = datetime.fromisoformat(by_name["current_week"].reset_at)
    assert week_reset.month == 6 and week_reset.day == 13
    assert week_reset.hour == 2 and week_reset.minute == 0


def test_claude_usage_dialog_wide_parses_block_bar_and_reset() -> None:
    # Real /usage overlay captured at ~217 cols; same format but no line-wrapping
    pane = _load_pane_fixture("cc_usage_dialog_wide.txt")
    now = datetime(2026, 6, 3, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    status = parse_claude_usage_pane(pane, now=now)
    assert len(status.windows) == 1
    assert status.windows[0].percent_used == 88.0
    reset_at = datetime.fromisoformat(status.windows[0].reset_at)
    assert reset_at.hour == 13 and reset_at.minute == 40
    t_until_minutes = (reset_at - now).total_seconds() / 60.0
    assert 3.5 * 60 < t_until_minutes < 4.0 * 60  # ~3h40m away
