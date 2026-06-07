"""Tests for DispatchStore, ScheduleStore, EscalationsStore (t049).

Verifies:
- each store notifies on changed ingest; identical re-ingest (same content,
  different as_of) does NOT notify
- ScheduleStore usage drill-in loader is invoked lazily and cached
- no Textual import in the three store modules
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from murder.app.service.client_api import (
    CalendarRunningAgent,
    CalendarScheduledTicket,
    DispatchSnapshot,
    EscalationSummary,
    EscalationsSnapshot,
    ScheduleSnapshot,
    SchedulerDecisionSummary,
    ScheduleTicketRow,
    TicketSummary,
    UsageGaugeDrillInSnapshot,
    UsageGaugeSummary,
    UsageBurnRow,
    UsageResetEvent,
)
from murder.app.tui.stores.dispatch import DispatchStore
from murder.app.tui.stores.escalations import EscalationsStore
from murder.app.tui.stores.schedule import ScheduleStore

_DT1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DT2 = datetime(2026, 1, 2, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ticket(tid: str = "t001", status: str = "open") -> TicketSummary:
    return TicketSummary(
        id=tid, title="Test", status=status, wave=1, harness=None, model=None
    )


def _dispatch_snap(
    tickets: tuple[TicketSummary, ...] = (),
    invalidation_key: str = "k1",
    as_of: datetime = _DT1,
) -> DispatchSnapshot:
    return DispatchSnapshot(tickets=tickets, as_of=as_of, invalidation_key=invalidation_key)


def _escalation(eid: int = 1) -> EscalationSummary:
    return EscalationSummary(
        id=eid,
        ticket_id="t001",
        severity=1,
        reason="test",
        to_recipient="planner",
        body_path=None,
    )


def _escalations_snap(
    active: tuple[EscalationSummary, ...] = (),
    history: tuple[EscalationSummary, ...] = (),
    invalidation_key: str = "k1",
    as_of: datetime = _DT1,
) -> EscalationsSnapshot:
    return EscalationsSnapshot(
        active=active, history=history, as_of=as_of, invalidation_key=invalidation_key
    )


def _gauge(harness: str = "claude_code", window_key: str = "5h", pct: float = 0.5) -> UsageGaugeSummary:
    return UsageGaugeSummary(
        harness=harness,
        window_key=window_key,
        pct=pct,
        t_until_reset_minutes=60.0,
        t_period_minutes=300.0,
    )


def _drill_in(harness: str = "claude_code", window_key: str = "5h") -> UsageGaugeDrillInSnapshot:
    return UsageGaugeDrillInSnapshot(
        harness=harness,
        window_key=window_key,
        sparkline="▁▂▃",
        recent_resets=(),
        burn_rows=(),
    )


def _schedule_snap(
    usage_gauges: tuple[UsageGaugeSummary, ...] = (),
    invalidation_key: str = "k1",
    as_of: datetime = _DT1,
) -> ScheduleSnapshot:
    return ScheduleSnapshot(
        scheduler_mode="auto",
        mode_rationale="test",
        active_tickets=(),
        recent_done_tickets=(),
        archived_tickets=(),
        scheduler_decisions=(),
        usage_gauges=usage_gauges,
        calendar_harnesses=(),
        running_agents=(),
        scheduled_tickets=(),
        as_of=as_of,
        invalidation_key=invalidation_key,
    )


# ---------------------------------------------------------------------------
# DispatchStore
# ---------------------------------------------------------------------------


def test_dispatch_notifies_on_first_ingest() -> None:
    store = DispatchStore()
    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_snapshot(_dispatch_snap((_ticket(),), "k1"))
    assert len(calls) == 1


def test_dispatch_no_notify_on_identical_reingest() -> None:
    """Same tickets + same invalidation_key but different as_of must NOT notify."""
    store = DispatchStore()
    t = _ticket()
    store.ingest_snapshot(_dispatch_snap((t,), "k1", _DT1))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_snapshot(_dispatch_snap((t,), "k1", _DT2))
    assert len(calls) == 0


def test_dispatch_notifies_on_content_change() -> None:
    store = DispatchStore()
    store.ingest_snapshot(_dispatch_snap((_ticket("t001"),), "k1"))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_snapshot(_dispatch_snap((_ticket("t002"),), "k2"))
    assert len(calls) == 1


def test_dispatch_snapshot_holds_tickets() -> None:
    store = DispatchStore()
    t = _ticket()
    store.ingest_snapshot(_dispatch_snap((t,), "k1"))
    snap = store.get_snapshot()
    assert snap.tickets == (t,)
    assert snap.invalidation_key == "k1"


# ---------------------------------------------------------------------------
# EscalationsStore
# ---------------------------------------------------------------------------


def test_escalations_notifies_on_first_ingest() -> None:
    store = EscalationsStore()
    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_snapshot(_escalations_snap((_escalation(),), (), "k1"))
    assert len(calls) == 1


def test_escalations_no_notify_on_identical_reingest() -> None:
    store = EscalationsStore()
    e = _escalation()
    store.ingest_snapshot(_escalations_snap((e,), (), "k1", _DT1))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_snapshot(_escalations_snap((e,), (), "k1", _DT2))
    assert len(calls) == 0


def test_escalations_notifies_on_content_change() -> None:
    store = EscalationsStore()
    store.ingest_snapshot(_escalations_snap((_escalation(1),), (), "k1"))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_snapshot(_escalations_snap((_escalation(2),), (), "k2"))
    assert len(calls) == 1


def test_escalations_snapshot_holds_active_and_history() -> None:
    store = EscalationsStore()
    e = _escalation()
    store.ingest_snapshot(_escalations_snap((e,), (e,), "k1"))
    snap = store.get_snapshot()
    assert snap.active == (e,)
    assert snap.history == (e,)
    assert snap.invalidation_key == "k1"


# ---------------------------------------------------------------------------
# ScheduleStore — ingest
# ---------------------------------------------------------------------------


def test_schedule_notifies_on_first_ingest() -> None:
    store = ScheduleStore(AsyncMock(return_value=_drill_in()))
    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_snapshot(_schedule_snap((_gauge(),), "k1"))
    assert len(calls) == 1


def test_schedule_no_notify_on_identical_reingest() -> None:
    store = ScheduleStore(AsyncMock(return_value=_drill_in()))
    g = _gauge()
    store.ingest_snapshot(_schedule_snap((g,), "k1", _DT1))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_snapshot(_schedule_snap((g,), "k1", _DT2))
    assert len(calls) == 0


def test_schedule_notifies_on_content_change() -> None:
    store = ScheduleStore(AsyncMock(return_value=_drill_in()))
    store.ingest_snapshot(_schedule_snap((_gauge(pct=0.3),), "k1"))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_snapshot(_schedule_snap((_gauge(pct=0.9),), "k2"))
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# ScheduleStore — usage drill-in lazy load and caching
# ---------------------------------------------------------------------------


def test_schedule_drill_in_loaded_once_and_cached() -> None:
    loader = AsyncMock(return_value=_drill_in())
    store = ScheduleStore(loader)
    store.ingest_snapshot(_schedule_snap((_gauge(),), "k1"))

    asyncio.run(store.request_drill_in("claude_code", "5h"))
    assert loader.call_count == 1
    snap = store.get_snapshot()
    assert len(snap.drill_ins) == 1

    asyncio.run(store.request_drill_in("claude_code", "5h"))
    assert loader.call_count == 1  # cache hit


def test_schedule_drill_in_notify_on_load() -> None:
    loader = AsyncMock(return_value=_drill_in())
    store = ScheduleStore(loader)
    store.ingest_snapshot(_schedule_snap((_gauge(),), "k1"))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    asyncio.run(store.request_drill_in("claude_code", "5h"))
    assert len(calls) == 1


def test_schedule_drill_in_evicted_when_gauge_removed() -> None:
    loader = AsyncMock(side_effect=[_drill_in(), _drill_in()])
    store = ScheduleStore(loader)
    store.ingest_snapshot(_schedule_snap((_gauge(),), "k1"))
    asyncio.run(store.request_drill_in("claude_code", "5h"))
    assert loader.call_count == 1
    assert len(store.get_snapshot().drill_ins) == 1

    # Ingest a snapshot with no gauges — cache entry must be evicted.
    store.ingest_snapshot(_schedule_snap((), "k2"))
    assert store.get_snapshot().drill_ins == ()

    # Re-add gauge — must require a fresh loader call.
    store.ingest_snapshot(_schedule_snap((_gauge(),), "k3"))
    asyncio.run(store.request_drill_in("claude_code", "5h"))
    assert loader.call_count == 2


def test_schedule_drill_in_noop_for_unknown_gauge() -> None:
    """request_drill_in does nothing if the gauge is not in the current snapshot."""
    loader = AsyncMock(return_value=_drill_in())
    store = ScheduleStore(loader)
    store.ingest_snapshot(_schedule_snap((), "k1"))  # no gauges

    asyncio.run(store.request_drill_in("claude_code", "5h"))
    assert loader.call_count == 0
    assert store.get_snapshot().drill_ins == ()


# ---------------------------------------------------------------------------
# No Textual import
# ---------------------------------------------------------------------------

_STORE_FILES = ["dispatch.py", "schedule.py", "escalations.py"]
_STORES_DIR = (
    Path(__file__).parent.parent.parent / "murder" / "app" / "tui" / "stores"
)


def test_no_textual_import_dispatch() -> None:
    source = (_STORES_DIR / "dispatch.py").read_text()
    assert not re.search(r"^\s*(import|from)\s+textual", source, re.MULTILINE)


def test_no_textual_import_schedule() -> None:
    source = (_STORES_DIR / "schedule.py").read_text()
    assert not re.search(r"^\s*(import|from)\s+textual", source, re.MULTILINE)


def test_no_textual_import_escalations() -> None:
    source = (_STORES_DIR / "escalations.py").read_text()
    assert not re.search(r"^\s*(import|from)\s+textual", source, re.MULTILINE)
