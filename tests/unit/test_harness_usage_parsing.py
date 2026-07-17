from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.llm.harnesses.codex import CodexAdapter
from murder.llm.harnesses.usage import (
    locate_codex_status_surface,
    parse_antigravity_usage_pane,
    parse_claude_usage_pane,
    parse_codex_status_pane,
)


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


def test_codex_status_parses_session_id_for_usage_probe_resume() -> None:
    status = parse_codex_status_pane(_codex_session_limit_pane())
    assert status.raw["session_id"] == "019e5c91-89a0-7ca1-9b8c-b407e537f7d6"


def test_usage_probe_invalid_resume_fixtures_are_detected() -> None:
    assert CodexAdapter().detects_invalid_resume(_load_pane_fixture("codex_resume_invalid.txt"))
    assert ClaudeCodeAdapter().detects_invalid_resume(_load_pane_fixture("cc_resume_invalid.txt"))


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


def test_codex_status_is_atomic_and_does_not_restore_an_old_window() -> None:
    old = _codex_session_limit_pane()
    old_5h_row = next(line for line in old.splitlines(keepends=True) if "5h limit:" in line)
    newest = old.replace(old_5h_row, "").replace("43% left", "44% left")
    status = parse_codex_status_pane(f"{old}\n{newest}")
    assert [(row.name, row.percent_used) for row in status.windows] == [("weekly", 56.0)]
    assert status.surface_complete is True


def test_codex_status_context_and_exact_stale_notice_are_typed() -> None:
    pane = Path(
        "tests/fixtures/harness_state/codex/status_context_window_stale_v0144.txt"
    ).read_text()
    status = parse_codex_status_pane(pane)
    assert status.context_window is not None
    assert status.context_window.used == 91.7
    assert status.context_window.limit == 353.0
    assert status.freshness.value == "advisory_stale"
    assert [notice.kind for notice in status.notices] == ["stale_limits"]


def test_codex_status_rejects_unbounded_hostile_percentages() -> None:
    status = parse_codex_status_pane("marketing copy: 90% used, limits are great")
    assert status.windows == []
    assert status.freshness.value == "unknown"
    assert locate_codex_status_surface("marketing copy: 90% used") is None


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


def test_claude_usage_dialog_ignores_sonnet_only_weekly_bar() -> None:
    # Max plan /usage can show three bars: session, weekly (all models), and
    # weekly (Sonnet only). Parser must read the first two and ignore Sonnet-only.
    pane = _load_pane_fixture("cc_usage_dialog_sonnet_only_bar.txt")
    now = datetime(2026, 6, 20, 18, 4, tzinfo=ZoneInfo("America/New_York"))
    status = parse_claude_usage_pane(pane, now=now)
    assert len(status.windows) == 2
    by_name = {w.name: w for w in status.windows}
    assert by_name["current_session"].percent_used == 100.0
    session_reset = datetime.fromisoformat(by_name["current_session"].reset_at)
    assert session_reset.hour == 18 and session_reset.minute == 19  # 6:19pm
    assert by_name["current_week"].percent_used == 17.0
    week_reset = datetime.fromisoformat(by_name["current_week"].reset_at)
    assert week_reset.month == 6 and week_reset.day == 27
    assert week_reset.hour == 1 and week_reset.minute == 59


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


def test_antigravity_usage_dialog_collapses_effort_variants() -> None:
    # agy 1.0.7 /usage "Model Quota" dialog: 8 raw rows (effort variants) must
    # collapse to 5 windows — variants sharing a base model and identical
    # (percent, reset) merge under the base name.
    pane = _load_pane_fixture("agy_usage_dialog.txt")
    now = datetime(2026, 6, 12, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    status = parse_antigravity_usage_pane(pane, now=now)
    assert status.harness == "antigravity"
    assert status.source == "slash:/usage"
    assert status.plan == "Antigravity Starter Quota"
    assert [w.name for w in status.windows] == [
        "Gemini 3.5 Flash",
        "Gemini 3.1 Pro",
        "Claude Sonnet 4.6",
        "Claude Opus 4.6",
        "GPT-OSS 120B",
    ]
    by_name = {w.name: w for w in status.windows}
    # "Quota available" + full bar -> 0% consumed, no reset hint.
    assert by_name["Gemini 3.5 Flash"].percent_used == 0.0
    assert by_name["Gemini 3.5 Flash"].reset_at is None
    assert by_name["Gemini 3.1 Pro"].percent_used == 0.0
    # "20% remaining · Refreshes in 12h 39m" -> 80% consumed, reset now+12h39m.
    for name in ("Claude Sonnet 4.6", "Claude Opus 4.6", "GPT-OSS 120B"):
        window = by_name[name]
        assert window.percent_used == 80.0
        reset_at = datetime.fromisoformat(window.reset_at)
        t_until_minutes = (reset_at - now).total_seconds() / 60.0
        assert t_until_minutes == 12 * 60 + 39


def test_antigravity_usage_dialog_grouped_weekly_limits() -> None:
    # agy 1.0.10 /usage "Models & Quota" dialog: model families are grouped,
    # and each group has a shared weekly limit. The bracketed bar carries the
    # precise remaining percent; the prose line is rounded.
    pane = _load_pane_fixture("agy_usage_dialog_grouped.txt")
    now = datetime(2026, 6, 23, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    status = parse_antigravity_usage_pane(pane, now=now)
    assert status.harness == "antigravity"
    assert status.source == "slash:/usage"
    assert status.plan == "Antigravity Starter Quota"
    assert [w.name for w in status.windows] == [
        "Gemini Models",
        "Claude and GPT Models",
    ]
    by_name = {w.name: w for w in status.windows}
    assert by_name["Gemini Models"].percent_used == 14.39
    reset_at = datetime.fromisoformat(by_name["Gemini Models"].reset_at)
    assert (reset_at - now).total_seconds() / 60.0 == 157 * 60 + 26
    assert by_name["Claude and GPT Models"].percent_used == 0.0
    assert by_name["Claude and GPT Models"].reset_at is None


def test_antigravity_usage_divergent_variants_keep_full_labels() -> None:
    pane = (
        "└ Model Quota\n"
        "\n"
        "  Gemini 3.5 Flash (Low)\n"
        "  ███ ░░░ 40%\n"
        "  40% remaining · Refreshes in 2h 5m\n"
        "\n"
        "  Gemini 3.5 Flash (High)\n"
        "  ███ ███ 100%\n"
        "  Quota available\n"
        "\n"
        "  esc to cancel\n"
    )
    now = datetime(2026, 6, 12, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    status = parse_antigravity_usage_pane(pane, now=now)
    assert [w.name for w in status.windows] == [
        "Gemini 3.5 Flash (Low)",
        "Gemini 3.5 Flash (High)",
    ]
    by_name = {w.name: w for w in status.windows}
    assert by_name["Gemini 3.5 Flash (Low)"].percent_used == 60.0
    reset_at = datetime.fromisoformat(by_name["Gemini 3.5 Flash (Low)"].reset_at)
    assert (reset_at - now).total_seconds() / 60.0 == 125.0
    assert by_name["Gemini 3.5 Flash (High)"].percent_used == 0.0
    assert by_name["Gemini 3.5 Flash (High)"].reset_at is None


def test_antigravity_usage_ignores_chrome_outside_dialog() -> None:
    # The banner model line + the status-bar model name must not become
    # windows; without a "Model Quota" anchor nothing parses at all.
    pane = (
        "  Antigravity CLI 1.0.7\n"
        "  lukeask@gmail.com (Antigravity Starter Quota)\n"
        "  Gemini 3.1 Pro (Low)\n"
        "  ~/Documents/code/murder\n"
        ">\n"
    )
    status = parse_antigravity_usage_pane(pane)
    assert status.windows == []
