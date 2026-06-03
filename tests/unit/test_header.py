"""Header bar render — cookbook then edge cases."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from textual.app import App, ComposeResult

from murder.service.client_api import CrowSessionSummary, CrowSnapshot, DispatchSnapshot, TicketSummary
from murder.tickets.status import TicketStatus
from murder.tui.crow_health import Health
from murder.tui.crows_view import CrowEntry, entries_from_snapshot
from murder.tui.header import (
    Header,
    crow_display_id,
    format_attention_segments,
    format_inflight_segment,
    format_view_tabs,
)
from murder.tui.themes import EVERFOREST_DARK_HARD, register_crow_themes


def _now() -> datetime:
    return datetime(2026, 6, 2, tzinfo=timezone.utc)


def _session(**kwargs: object) -> CrowSessionSummary:
    defaults = dict(
        agent_id="crow-t001",
        role="crow",
        ticket_id="t001",
        ticket_title="Fix thing",
        status="running",
        session_name="murder_demo_crow_t001",
        harness="cursor",
        last_seen=None,
        started_at=None,
        ticket_status="in_progress",
    )
    defaults.update(kwargs)
    return CrowSessionSummary(**defaults)  # type: ignore[arg-type]


def _snapshot(*sessions: CrowSessionSummary) -> CrowSnapshot:
    return CrowSnapshot(sessions=sessions, as_of=_now(), invalidation_key="k")


def _entry(**kwargs: object) -> CrowEntry:
    defaults = dict(
        agent_id="crow-t001",
        ticket_id="t001",
        ticket_title="Fix thing",
        harness="cursor",
        status="running",
        session="murder_demo_crow_t001",
        health=Health.GREEN,
    )
    defaults.update(kwargs)
    return CrowEntry(**defaults)  # type: ignore[arg-type]


def _dispatch(*statuses: TicketStatus) -> DispatchSnapshot:
    tickets = tuple(
        TicketSummary(
            id=f"t{i:03d}",
            title=f"ticket {i}",
            status=status,
            wave=1,
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


def test_view_tabs_active_tab_uses_accent() -> None:
    accent = EVERFOREST_DARK_HARD.accent
    rendered = format_view_tabs("planning", accent)
    assert "[1 planning]" not in rendered
    assert f"[b {accent}]planning[/]" in rendered


def test_inflight_renders_ticket_ids() -> None:
    entries = entries_from_snapshot(
        _snapshot(
            _session(ticket_id="t012", session_name="murder_demo_crow_t012"),
            _session(ticket_id="t034", session_name="murder_demo_crow_t034"),
        )
    )
    assert format_inflight_segment(entries) == "▶2 t012 t034"


def test_inflight_truncates_long_rogue_name() -> None:
    entry = _entry(
        ticket_id="",
        agent_id="rogue-cursor-test",
        session="murder_repo_crow_cursor_rogue_test",
    )
    rendered = crow_display_id(entry)
    assert rendered.endswith("…")
    assert len(rendered) == 13  # 12 chars + ellipsis


def test_inflight_overflow_collapses_to_plus_k() -> None:
    sessions = tuple(
        _session(
            agent_id=f"crow-{i}",
            ticket_id=f"t{i:03d}",
            session_name=f"murder_demo_crow_t{i:03d}",
        )
        for i in range(8)
    )
    entries = entries_from_snapshot(_snapshot(*sessions))
    assert format_inflight_segment(entries) == "▶8 t000 t001 t002 +5"


def test_attention_segments_zero_suppressed() -> None:
    assert format_attention_segments({"blocked": 0, "failed": 0}) == []
    assert format_attention_segments({"blocked": 2, "failed": 0}) == ["⚠2"]
    assert format_attention_segments({"blocked": 0, "failed": 1}) == ["✗1"]
    assert format_attention_segments({"blocked": 2, "failed": 1}) == ["⚠2", "✗1"]


def test_header_refresh_omits_legacy_status_counts() -> None:
    header = Header("demo")

    async def _run() -> None:
        app = _HeaderApp(header)
        async with app.run_test() as pilot:
            header.refresh_from_snapshot(
                _dispatch(
                    TicketStatus.DRAFT,
                    TicketStatus.PLANNED,
                    TicketStatus.READY,
                    TicketStatus.IN_PROGRESS,
                    TicketStatus.BLOCKED,
                    TicketStatus.DONE,
                    TicketStatus.FAILED,
                    TicketStatus.ARCHIVED,
                )
            )
            await pilot.pause()
            text = str(header.render())
            assert "draft:" not in text
            assert "planned:" not in text
            assert "ready:" not in text
            assert "in_progress:" not in text
            assert "done:" not in text
            assert "archived:" not in text
            assert "⚠1" in text
            assert "✗1" in text

    asyncio.run(_run())


def test_header_cold_start_no_crash() -> None:
    header = Header("demo")

    async def _run() -> None:
        app = _HeaderApp(header)
        async with app.run_test() as pilot:
            header.refresh_from_snapshot(
                DispatchSnapshot(tickets=(), as_of=_now(), invalidation_key="empty")
            )
            header.set_view("planning")
            await pilot.pause()
            text = str(header.render())
            assert "planning" in text
            assert "▶" not in text
            assert "⚠" not in text
            assert "✗" not in text

    asyncio.run(_run())
