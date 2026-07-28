from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from murder.llm.harness_control.adapters.pi import _model_scope
from murder.llm.harnesses.transcripts import _PaneScrollback, parse_frames
from murder.llm.harnesses.usage import parse_codex_status_pane

FIXTURES = Path("tests/fixtures/harness_geometry")
NOW = datetime(2026, 5, 17, 12, 0, tzinfo=ZoneInfo("UTC"))


def test_width_independent_semantics_and_fixed_width_compatibility_boundary() -> None:
    expected_windows = [("5h", 25.0), ("weekly", 60.0)]
    expected_resets = [
        "2026-05-17T21:29:00+00:00",
        "2026-05-18T14:49:00+00:00",
    ]
    for width in (54, 84, 220, 260):
        fixture = (FIXTURES / f"codex_status_{width}.txt").read_text()
        fixture = fixture.replace("OpenAI Codex", "\x1b[1;36mOpenAI Codex\x1b[0m")
        fixture = "\n".join(f"{line}   " for line in fixture.splitlines())
        status = parse_codex_status_pane(fixture, now=NOW)
        assert status.surface_complete is True
        assert [(window.name, window.percent_used) for window in status.windows] == expected_windows
        assert [window.reset_at for window in status.windows] == expected_resets
        assert status.context_window is not None
        assert (
            status.context_window.percent_used,
            status.context_window.used,
            status.context_window.limit,
        ) == (20.0, 20.0, 100.0)
        assert status.raw["session_id"] == "019e5c91-89a0-7ca1-9b8c-b407e537f7d6"

        transcript = parse_frames("codex", [f"• useful semantic answer\n{fixture}"], pane_height=50)
        serialized = json.dumps(transcript["segments"], sort_keys=True)
        assert "useful semantic answer" in serialized
        for chrome in ("5h limit", "Weekly limit", "Session:", "Account:", "resets 14:49"):
            assert chrome not in serialized

    models = (
        "deepseek/deepseek-v4-pro",
        "google/gemini-3.1-pro-preview",
        "moonshotai/kimi-k2.6",
        "xiaomi/mimo-v2.5-pro",
    )
    logical_scope = f"Model scope: {','.join(models)} (Ctrl+P to cycle)"
    for width in (48, 80, 220, 260):
        physical = [
            logical_scope[index : index + width]
            for index in range(0, len(logical_scope), width)
        ]
        assert _model_scope(physical + [""])["available"] == models

    # Remaining compatibility boundary: transcript reconciliation assigns
    # identity to physical rows. Rewrapping one logical line at another width
    # must begin a new epoch; it cannot be aligned as ordinary scrolling.
    scrollback = _PaneScrollback(live_window=1)
    scrollback.feed("alpha beta gamma\nstable footer")
    scrollback.feed("alpha beta\ngamma\nstable footer")
    assert scrollback.last_reset is True
    assert scrollback.epoch == 1
