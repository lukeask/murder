"""Tests for PlansStore, NotesStore, ReportsStore (t048).

Verifies:
- list ingest notifies on change; identical re-ingest (same content, different
  as_of) does NOT notify
- requesting a body calls the loader once and caches it; second request is
  a cache hit (no second loader call)
- body cache is evicted when the item's version key changes on next ingest
- no Textual import in the module
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, call

from murder.app.service.client_api import (
    NoteDisplaySnapshot,
    NotesSnapshot,
    NoteSummary,
    PlanDisplaySnapshot,
    PlansSnapshot,
    PlanSummary,
    ReportDisplaySnapshot,
    ReportsSnapshot,
    ReportSummary,
)
from murder.app.tui.stores.documents import (
    NotesStore,
    PlansStore,
    ReportsStore,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DT1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_DT2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
_DT3 = datetime(2026, 1, 3, tzinfo=timezone.utc)


def _plans_snap(
    plans: tuple[PlanSummary, ...] = (),
    invalidation_key: str = "k1",
    as_of: datetime = _DT1,
) -> PlansSnapshot:
    return PlansSnapshot(plans=plans, invalidation_key=invalidation_key, as_of=as_of)


def _notes_snap(
    notes: tuple[NoteSummary, ...] = (),
    invalidation_key: str = "k1",
    as_of: datetime = _DT1,
) -> NotesSnapshot:
    return NotesSnapshot(notes=notes, invalidation_key=invalidation_key, as_of=as_of)


def _reports_snap(
    reports: tuple[ReportSummary, ...] = (),
    invalidation_key: str = "k1",
    as_of: datetime = _DT1,
) -> ReportsSnapshot:
    return ReportsSnapshot(reports=reports, invalidation_key=invalidation_key, as_of=as_of)


def _plan(name: str = "plan-a", revision: int = 1) -> PlanSummary:
    return PlanSummary(name=name, status="active", revision_count=revision, sync_state="clean")


def _note(name: str = "note-a", updated: datetime = _DT1) -> NoteSummary:
    return NoteSummary(name=name, char_count=100, updated_at=updated)


def _report(name: str = "report-a", updated: datetime = _DT1) -> ReportSummary:
    return ReportSummary(name=name, char_count=200, updated_at=updated)


def _plan_display(name: str = "plan-a", body: str = "# plan") -> PlanDisplaySnapshot:
    return PlanDisplaySnapshot(name=name, markdown=body)


def _note_display(name: str = "note-a", body: str = "# note") -> NoteDisplaySnapshot:
    return NoteDisplaySnapshot(name=name, markdown=body)


def _report_display(name: str = "report-a", body: str = "# report") -> ReportDisplaySnapshot:
    return ReportDisplaySnapshot(name=name, markdown=body)


# ---------------------------------------------------------------------------
# PlansStore — list ingest
# ---------------------------------------------------------------------------


def test_plans_store_notifies_on_first_ingest() -> None:
    store = PlansStore(AsyncMock(return_value=None))
    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_list(_plans_snap((_plan(),), "key1", _DT1))
    assert len(calls) == 1


def test_plans_store_no_notify_on_identical_reingest() -> None:
    """Same items + same invalidation_key but different as_of must NOT notify."""
    store = PlansStore(AsyncMock(return_value=None))
    store.ingest_list(_plans_snap((_plan(),), "key1", _DT1))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    # Same content, advanced as_of (simulates next poll tick)
    store.ingest_list(_plans_snap((_plan(),), "key1", _DT2))
    assert len(calls) == 0


def test_plans_store_notifies_on_content_change() -> None:
    store = PlansStore(AsyncMock(return_value=None))
    store.ingest_list(_plans_snap((_plan(revision=1),), "key1", _DT1))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_list(_plans_snap((_plan(revision=2),), "key2", _DT2))
    assert len(calls) == 1


def test_plans_store_snapshot_holds_items() -> None:
    store = PlansStore(AsyncMock(return_value=None))
    p = _plan()
    store.ingest_list(_plans_snap((p,), "key1"))
    snap = store.get_snapshot()
    assert snap.items == (p,)
    assert snap.invalidation_key == "key1"
    assert snap.bodies == ()


# ---------------------------------------------------------------------------
# PlansStore — body loading and caching
# ---------------------------------------------------------------------------


def test_plans_store_body_loaded_once_and_cached() -> None:
    """Loader is called exactly once; second request_body is a no-op."""
    loader = AsyncMock(return_value=_plan_display("plan-a", "# body"))
    store = PlansStore(loader)
    store.ingest_list(_plans_snap((_plan("plan-a"),), "key1"))

    asyncio.run(store.request_body("plan-a"))
    assert loader.call_count == 1
    assert ("plan-a", "# body") in store.get_snapshot().bodies

    asyncio.run(store.request_body("plan-a"))
    assert loader.call_count == 1  # cache hit — loader not called again


def test_plans_store_body_notify_on_load() -> None:
    """Loading a body triggers a snapshot change notification."""
    loader = AsyncMock(return_value=_plan_display("plan-a", "# md"))
    store = PlansStore(loader)
    store.ingest_list(_plans_snap((_plan("plan-a"),), "key1"))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    asyncio.run(store.request_body("plan-a"))
    assert len(calls) == 1


def test_plans_store_body_cache_evicted_on_revision_change() -> None:
    """When revision_count changes, cached body is invalidated and loader re-called."""
    loader = AsyncMock(
        side_effect=[
            _plan_display("plan-a", "# v1"),
            _plan_display("plan-a", "# v2"),
        ]
    )
    store = PlansStore(loader)

    store.ingest_list(_plans_snap((_plan("plan-a", revision=1),), "key1"))
    asyncio.run(store.request_body("plan-a"))
    assert loader.call_count == 1
    assert ("plan-a", "# v1") in store.get_snapshot().bodies

    # Ingest new list with incremented revision — cache entry must be evicted
    store.ingest_list(_plans_snap((_plan("plan-a", revision=2),), "key2"))
    assert store.get_snapshot().bodies == ()  # evicted

    asyncio.run(store.request_body("plan-a"))
    assert loader.call_count == 2
    assert ("plan-a", "# v2") in store.get_snapshot().bodies


def test_plans_store_body_none_loader_does_not_cache() -> None:
    """If the loader returns None, no entry is cached and snapshot unchanged."""
    loader = AsyncMock(return_value=None)
    store = PlansStore(loader)
    store.ingest_list(_plans_snap((_plan("plan-a"),), "key1"))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    asyncio.run(store.request_body("plan-a"))
    assert store.get_snapshot().bodies == ()
    assert len(calls) == 0  # no change → no notify


# ---------------------------------------------------------------------------
# PlansStore — set_selected
# ---------------------------------------------------------------------------


def test_plans_store_set_selected_notifies() -> None:
    store = PlansStore(AsyncMock(return_value=None))
    store.ingest_list(_plans_snap((_plan("plan-a"),), "key1"))
    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.set_selected("plan-a")
    assert store.get_snapshot().selected_name == "plan-a"
    assert len(calls) == 1


def test_plans_store_set_selected_same_no_notify() -> None:
    store = PlansStore(AsyncMock(return_value=None))
    store.set_selected("plan-a")  # set it
    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.set_selected("plan-a")  # same name again
    assert len(calls) == 0


# ---------------------------------------------------------------------------
# NotesStore — basic smoke (parallel logic, spot-check version eviction)
# ---------------------------------------------------------------------------


def test_notes_store_no_notify_on_identical_reingest() -> None:
    store = NotesStore(AsyncMock(return_value=None))
    note = _note("note-a", _DT1)
    store.ingest_list(_notes_snap((note,), "k1", _DT1))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_list(_notes_snap((note,), "k1", _DT2))  # only as_of advances
    assert len(calls) == 0


def test_notes_store_body_evicted_on_updated_at_change() -> None:
    loader = AsyncMock(
        side_effect=[
            _note_display("note-a", "old body"),
            _note_display("note-a", "new body"),
        ]
    )
    store = NotesStore(loader)

    store.ingest_list(_notes_snap((_note("note-a", _DT1),), "k1"))
    asyncio.run(store.request_body("note-a"))
    assert loader.call_count == 1

    store.ingest_list(_notes_snap((_note("note-a", _DT2),), "k2"))
    assert store.get_snapshot().bodies == ()  # evicted

    asyncio.run(store.request_body("note-a"))
    assert loader.call_count == 2


# ---------------------------------------------------------------------------
# ReportsStore — basic smoke
# ---------------------------------------------------------------------------


def test_reports_store_no_notify_on_identical_reingest() -> None:
    store = ReportsStore(AsyncMock(return_value=None))
    report = _report("report-a", _DT1)
    store.ingest_list(_reports_snap((report,), "k1", _DT1))

    calls: list[None] = []
    store.subscribe(lambda: calls.append(None))

    store.ingest_list(_reports_snap((report,), "k1", _DT2))
    assert len(calls) == 0


def test_reports_store_body_loaded_and_cached() -> None:
    loader = AsyncMock(return_value=_report_display("report-a", "# rpt"))
    store = ReportsStore(loader)
    store.ingest_list(_reports_snap((_report("report-a"),), "k1"))

    asyncio.run(store.request_body("report-a"))
    assert loader.call_count == 1
    asyncio.run(store.request_body("report-a"))
    assert loader.call_count == 1


# ---------------------------------------------------------------------------
# No Textual import
# ---------------------------------------------------------------------------


def test_no_textual_import() -> None:
    """The documents module must be headless — no Textual dependency."""
    source = (
        Path(__file__).parent.parent.parent
        / "murder"
        / "app"
        / "tui"
        / "stores"
        / "documents.py"
    ).read_text()
    assert not re.search(r"^\s*(import|from)\s+textual", source, re.MULTILINE)
