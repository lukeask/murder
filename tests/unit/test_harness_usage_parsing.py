from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from murder.harnesses.usage import parse_codex_status_pane


def _load_pane_fixture(name: str) -> str:
    fixture = Path(f"tests/fixtures/harness_panes/{name}").read_text()
    return "\n".join(line for line in fixture.splitlines() if not line.startswith("# source"))


def _codex_session_limit_pane() -> str:
    return _load_pane_fixture("codex_session_limit.txt")


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
