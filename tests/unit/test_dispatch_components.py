"""Tests for dispatch-domain StoreComponent widgets and store-derived fields.

COOKBOOK = a StoreComponent binds to a store and re-renders on change.
EDGE CASES = derived-field stability (no spurious notify) and sort/count
corner cases of the pure derivation helpers.

All tests are purely headless — no real Textual app, no asyncio event loop.
We use BaseStore high-fidelity stores and minimal StoreComponent subclasses to
exercise the subscription lifecycle in pure Python.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from murder.app.tui.stores.dispatch import DispatchStore, _compute_attention_counts
from murder.app.tui.stores.schedule import ScheduleStore, _sort_schedule_rows
from tests.support.factories import (
    factory_dispatch_snapshot,
    factory_schedule_snapshot,
    factory_schedule_ticket_row,
    factory_ticket_summary,
)
from tests.support.simulators import FakeAsyncLoader

_DT1 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
_DT2 = datetime(2026, 1, 2, 12, 0, tzinfo=timezone.utc)


def _blocked_failed_tickets() -> tuple[Any, ...]:
    from murder.app.service.client_api import TicketSummary
    from murder.work.tickets.status import TicketStatus

    return (
        TicketSummary(
            id="t1", title="A", status=TicketStatus.BLOCKED, wave=1, harness=None, model=None
        ),
        TicketSummary(
            id="t2", title="B", status=TicketStatus.FAILED, wave=1, harness=None, model=None
        ),
        TicketSummary(
            id="t3", title="C", status=TicketStatus.BLOCKED, wave=1, harness=None, model=None
        ),
        TicketSummary(
            id="t4", title="D", status=TicketStatus.PLANNED, wave=1, harness=None, model=None
        ),
    )


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_store_component_subscribes_on_mount_unsubscribes_on_unmount() -> None:
    """A StoreComponent self-subscribes on mount and stops rendering after unmount."""
    from murder.app.tui.components import StoreComponent

    class _H(StoreComponent):
        def __init__(self) -> None:
            self.rendered: list[Any] = []

        def refresh_from_snapshot(self, snapshot: Any, **_kwargs: Any) -> None:
            self.rendered.append(snapshot)

    store = DispatchStore()
    h = _H()
    h.bind_stores(dispatch=store)
    h.on_mount()

    initial = len(h.rendered)
    store.ingest_snapshot(factory_dispatch_snapshot((factory_ticket_summary(),), "k1"))
    assert len(h.rendered) == initial + 1

    h.on_unmount()
    h.rendered.clear()
    store.ingest_snapshot(factory_dispatch_snapshot((factory_ticket_summary(),), "k2"))
    assert h.rendered == []


# ============================================================
# === EDGE CASES =============================================
# ============================================================


def test_dispatch_store_attention_counts_count_blocked_and_failed() -> None:
    """blocked/failed tickets are counted; other statuses are ignored."""
    store = DispatchStore()
    store.ingest_snapshot(factory_dispatch_snapshot(_blocked_failed_tickets()))
    counts = dict(store.get_snapshot().attention_counts)
    assert counts["blocked"] == 2
    assert counts["failed"] == 1


def test_dispatch_store_attention_counts_stable_across_reingest() -> None:
    """Identical tickets + key but different as_of must NOT notify (counts unchanged)."""
    from murder.app.service.client_api import TicketSummary
    from murder.work.tickets.status import TicketStatus

    ticket = TicketSummary(
        id="t1", title="A", status=TicketStatus.BLOCKED, wave=1, harness=None, model=None
    )
    store = DispatchStore()
    store.ingest_snapshot(factory_dispatch_snapshot((ticket,), "k1", _DT1))
    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))
    store.ingest_snapshot(factory_dispatch_snapshot((ticket,), "k1", _DT2))
    assert len(calls) == 0, "attention_counts must be stable for same content"


def test_schedule_store_sorted_rows_merges_all_buckets_newest_first() -> None:
    """sorted_rows merges active + recent_done + archived, sorted newest-first."""
    active = factory_schedule_ticket_row("t001", last_update_at=_DT1)
    done = factory_schedule_ticket_row("t002", last_update_at=_DT2)
    archived = factory_schedule_ticket_row("t003", last_update_at=_DT1)
    store = ScheduleStore(FakeAsyncLoader())
    store.ingest_snapshot(
        factory_schedule_snapshot(
            active_tickets=(active,),
            recent_done_tickets=(done,),
            archived_tickets=(archived,),
        )
    )
    rows = store.get_snapshot().sorted_rows
    assert {r.id for r in rows} == {"t001", "t002", "t003"}
    # t002 has the latest last_update_at, so it sorts first.
    assert rows[0].id == "t002"


def test_schedule_store_sorted_rows_stable_across_reingest() -> None:
    """sorted_rows must be stable for same content (no spurious notify)."""
    store = ScheduleStore(FakeAsyncLoader())
    store.ingest_snapshot(factory_schedule_snapshot(invalidation_key="k1", as_of=_DT1))
    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))
    store.ingest_snapshot(factory_schedule_snapshot(invalidation_key="k1", as_of=_DT2))
    assert len(calls) == 0, "sorted_rows for empty tix must be stable"


def test_sort_schedule_rows_empty_is_empty() -> None:
    assert _sort_schedule_rows(()) == ()


def test_sort_schedule_rows_orders_newest_first() -> None:
    older = factory_schedule_ticket_row("t001", last_update_at=_DT1)
    newer = factory_schedule_ticket_row("t002", last_update_at=_DT2)
    result = _sort_schedule_rows((older, newer))
    assert [r.id for r in result] == ["t002", "t001"]


def test_sort_schedule_rows_ties_broken_deterministically() -> None:
    """Rows with equal last_update_at sort deterministically (stable by id)."""
    r1 = factory_schedule_ticket_row("t001", last_update_at=_DT1)
    r2 = factory_schedule_ticket_row("t002", last_update_at=_DT1)
    assert {r.id for r in _sort_schedule_rows((r2, r1))} == {"t001", "t002"}


def test_compute_attention_counts_empty_is_zeroed() -> None:
    assert dict(_compute_attention_counts(())) == {"blocked": 0, "failed": 0}


def test_compute_attention_counts_with_enum_status() -> None:
    result = dict(_compute_attention_counts(_blocked_failed_tickets()))
    assert result["blocked"] == 2
    assert result["failed"] == 1


def test_compute_attention_counts_accepts_string_status() -> None:
    """String statuses (as used by some bridge callers) are handled gracefully."""
    from murder.app.service.client_api import TicketSummary

    tickets = (
        TicketSummary(id="t1", title="A", status="blocked", wave=1, harness=None, model=None),
        TicketSummary(id="t2", title="B", status="failed", wave=1, harness=None, model=None),
        TicketSummary(id="t3", title="C", status="planned", wave=1, harness=None, model=None),
    )
    result = dict(_compute_attention_counts(tickets))
    assert result["blocked"] == 1
    assert result["failed"] == 1
