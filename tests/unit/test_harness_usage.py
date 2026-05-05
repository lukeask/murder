from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from murder.harnesses.usage import parse_claude_usage_pane, parse_codex_status_pane


def test_parse_claude_usage_panel_standardizes_session_window() -> None:
    pane = """
  Session
  Total cost:            $0.1234
  Total duration (API):  3s
  Total duration (wall): 8s
  Total code changes:    12 lines added, 4 lines removed
  Usage:                 10 input, 20 output, 30 cache read, 40 cache write

  Current session
  ███████████████████████████▌                       55% used
  Resets 7:20pm (America/New_York)
"""

    status = parse_claude_usage_pane(
        pane,
        fetched_at="2026-05-04T12:00:00+00:00",
        now=datetime(2026, 5, 4, 12, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    assert status.harness == "claude_code"
    assert status.source == "slash:/usage"
    assert status.session is not None
    assert status.session.cost_usd == 0.1234
    assert status.session.input_tokens == 10
    assert status.session.output_tokens == 20
    assert status.session.cache_read_tokens == 30
    assert status.session.cache_write_tokens == 40
    assert status.session.lines_added == 12
    assert status.session.lines_removed == 4
    assert status.windows[0].name == "current_session"
    assert status.windows[0].percent_used == 55.0
    assert status.windows[0].reset_at == "2026-05-04T19:20:00-04:00"


def test_parse_codex_status_panel_standardizes_usage_window() -> None:
    pane = """
  Usage
  Weekly limit          42% used
  Resets 9:15am (America/New_York)
"""

    status = parse_codex_status_pane(
        pane,
        fetched_at="2026-05-04T12:00:00+00:00",
        now=datetime(2026, 5, 4, 8, 0, tzinfo=ZoneInfo("America/New_York")),
    )

    assert status.harness == "codex"
    assert status.source == "slash:/status"
    assert status.windows[0].name == "weekly"
    assert status.windows[0].percent_used == 42.0
    assert status.windows[0].reset_at == "2026-05-04T09:15:00-04:00"
