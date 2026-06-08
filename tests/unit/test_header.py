"""Header bar render — cookbook then edge cases."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from textual.app import App, ComposeResult

from murder.app.service.client_api import (
    DispatchSnapshot,
    TicketSummary,
    UsageGaugeSummary,
)
from murder.work.tickets.status import TicketStatus
from murder.app.tui.crows_view import entries_from_snapshot
from murder.app.tui.header import (
    Header,
    compose_header_line,
    crow_display_id,
    format_attention_segments,
    format_inflight_segment,
    format_usage_segments,
    format_view_tabs,
    pick_soonest_per_harness,
)
from murder.app.tui.themes import EVERFOREST_DARK_HARD, register_crow_themes
from tests.support.factories import (
    factory_crow_entry,
    factory_crow_session,
    factory_crow_snapshot,
)


def _now() -> datetime:
    return datetime(2026, 6, 2, tzinfo=timezone.utc)


def _dispatch(*statuses: TicketStatus) -> DispatchSnapshot:
    tickets = tuple(
        TicketSummary(
            id=f"t{i:03d}",
            title=f"ticket {i}",
            status=status,
            harness="cursor",
            model=None,
        )
        for i, status in enumerate(statuses, start=1)
    )
    return DispatchSnapshot(tickets=tickets, as_of=_now(), invalidation_key="d")


class _HeaderApp(App[None]):
    def __init__(self, header: Header, *, theme: str = "everforest-dark-hard") -> None:
        super().__init__()
        register_crow_themes(self)
        self.theme = theme
        self._header = header

    def compose(self) -> ComposeResult:
        yield self._header


def test_view_tabs_plain_words_no_numbers() -> None:
    assert format_view_tabs("crows", None) == "planning  [b]crows[/]  dispatch"


def test_compose_header_line_right_aligns_status_group() -> None:
    rendered = compose_header_line("murder · demo · planning  crows  dispatch", "▶2 t001 t002", 60)
    assert rendered.endswith("▶2 t001 t002")
    assert "dispatch" in rendered
    assert "dispatch ▶2" not in rendered


def test_compose_header_line_drops_right_group_when_width_too_small() -> None:
    left = "murder · demo · planning  crows  dispatch"
    assert compose_header_line(left, "▶2 t001 t002", len(left)) == left


def test_view_tabs_active_tab_uses_accent() -> None:
    accent = EVERFOREST_DARK_HARD.accent
    rendered = format_view_tabs("planning", accent)
    assert "[1 planning]" not in rendered
    assert f"[b {accent}]planning[/]" in rendered


def test_inflight_renders_ticket_ids() -> None:
    entries = entries_from_snapshot(
        factory_crow_snapshot(
            factory_crow_session(ticket_id="t012", session_name="murder_demo_crow_t012"),
            factory_crow_session(ticket_id="t034", session_name="murder_demo_crow_t034"),
        )
    )
    assert format_inflight_segment(entries) == "▶2 t012 t034"


def test_inflight_truncates_long_rogue_name() -> None:
    entry = factory_crow_entry(
        ticket_id="",
        agent_id="rogue-cursor-test",
        session="murder_repo_crow_cursor_rogue_test",
    )
    rendered = crow_display_id(entry)
    assert rendered.endswith("…")
    assert len(rendered) == 13  # 12 chars + ellipsis


def test_inflight_overflow_collapses_to_plus_k() -> None:
    sessions = tuple(
        factory_crow_session(
            agent_id=f"crow-{i}",
            ticket_id=f"t{i:03d}",
            session_name=f"murder_demo_crow_t{i:03d}",
        )
        for i in range(8)
    )
    entries = entries_from_snapshot(factory_crow_snapshot(*sessions))
    assert format_inflight_segment(entries) == "▶8 t000 t001 t002 +5"


def test_usage_picks_soonest_reset_per_harness() -> None:
    gauges = (
        UsageGaugeSummary("codex", "5h", 26.0, 262.0, 300.0),
        UsageGaugeSummary("codex", "weekly", 10.0, 10_080.0, 10_080.0),
        UsageGaugeSummary("claude_code", "current_session", 40.0, 50.0, 300.0),
    )
    picked = pick_soonest_per_harness(gauges)
    assert picked["codex"].window_key == "5h"
    assert picked["claude_code"].window_key == "current_session"


def test_usage_render_matches_gauge_clocks() -> None:
    gauges = (
        UsageGaugeSummary("claude_code", "current_session", 26.4, 262.0, 300.0),
        UsageGaugeSummary("codex", "5h", 28.0, 234.0, 300.0),
        UsageGaugeSummary("cursor", "auto_composer", 88.1, 11 * 24 * 60.0, 43_200.0),
    )
    assert format_usage_segments(gauges, colorize=False) == [
        "claude 26% 4h22m",
        "codex 28% 3h54m",
        "cursor 88% 11d",
    ]


def test_usage_empty_gauges_no_segments() -> None:
    assert format_usage_segments(()) == []


def test_attention_segments_zero_suppressed() -> None:
    assert format_attention_segments({"blocked": 0, "failed": 0}) == []
    assert format_attention_segments({"blocked": 2, "failed": 0}) == ["⚠2"]
    assert format_attention_segments({"blocked": 0, "failed": 1}) == ["✗1"]
    assert format_attention_segments({"blocked": 2, "failed": 1}) == ["⚠2", "✗1"]




