"""Unit tests for the RosterStore and its CrowSnapshot projection.

COOKBOOK = ingest a snapshot, read back the projected/sorted entries.
EDGE CASES = terminal-agent skipping, failed-stale visibility windows,
health derivation, change-detection on notify.
"""

from __future__ import annotations

import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from murder.app.tui.crow_health import Health
from murder.app.tui.stores import roster as roster_mod
from murder.app.tui.stores.roster import (
    FAILED_STALE_AFTER,
    RosterStore,
    entries_from_snapshot,
)
from tests.support.factories import factory_crow_session, factory_crow_snapshot

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


# ============================================================
# === COOKBOOK ===============================================
# ============================================================


def test_entries_from_snapshot_includes_running_crow() -> None:
    snap = factory_crow_snapshot(factory_crow_session())
    entries = entries_from_snapshot(snap, now=_NOW)
    assert len(entries) == 1
    assert entries[0].agent_id == "crow-t001"


def test_roster_store_ingest_produces_entries() -> None:
    store = RosterStore()
    snap = factory_crow_snapshot(factory_crow_session())
    store.ingest_snapshot(snap, now=_NOW)
    snapshot = store.get_snapshot()
    assert len(snapshot.entries) == 1
    assert snapshot.entries[0].agent_id == "crow-t001"
    assert snapshot.invalidation_key == "k"


# ============================================================
# === EDGE CASES =============================================
# ============================================================


def test_entries_from_snapshot_skips_handlers() -> None:
    snap = factory_crow_snapshot(
        factory_crow_session(agent_id="crow_handler-t001", role="crow_handler", harness=""),
        factory_crow_session(agent_id="planning_handler-plan", role="planning_handler", harness=""),
        factory_crow_session(),
    )
    entries = entries_from_snapshot(snap, now=_NOW)
    assert [e.agent_id for e in entries] == ["crow-t001"]


@pytest.mark.parametrize("terminal_status", ["done", "dead"])
def test_entries_from_snapshot_skips_terminal_agents(terminal_status: str) -> None:
    snap = factory_crow_snapshot(
        factory_crow_session(status=terminal_status),
        factory_crow_session(agent_id="crow-t002", status="running"),
    )
    entries = entries_from_snapshot(snap, now=_NOW)
    assert [e.agent_id for e in entries] == ["crow-t002"]


def test_entries_from_snapshot_includes_rogue() -> None:
    snap = factory_crow_snapshot(factory_crow_session(role="rogue", agent_id="rogue-tailwall"))
    entries = entries_from_snapshot(snap, now=_NOW)
    assert len(entries) == 1
    assert entries[0].agent_id == "rogue-tailwall"


def test_entries_from_snapshot_status_sort_order() -> None:
    snap = factory_crow_snapshot(
        factory_crow_session(agent_id="a", status="idle"),
        factory_crow_session(agent_id="b", status="escalating"),
        factory_crow_session(agent_id="c", status="running"),
        factory_crow_session(agent_id="d", status="blocked"),
    )
    entries = entries_from_snapshot(snap, now=_NOW)
    statuses = [e.status for e in entries]
    assert statuses == ["escalating", "blocked", "running", "idle"]


def test_entries_from_snapshot_failed_stale_hidden() -> None:
    stale_time = _NOW - FAILED_STALE_AFTER - timedelta(seconds=1)
    snap = factory_crow_snapshot(
        factory_crow_session(status="failed", last_seen=stale_time, ticket_status="done")
    )
    assert entries_from_snapshot(snap, now=_NOW) == []


def test_entries_from_snapshot_failed_recent_shown() -> None:
    recent = _NOW - timedelta(hours=1)
    snap = factory_crow_snapshot(
        factory_crow_session(status="failed", last_seen=recent, ticket_status="done")
    )
    entries = entries_from_snapshot(snap, now=_NOW)
    assert len(entries) == 1


def test_entries_from_snapshot_failed_active_ticket_always_shown() -> None:
    stale_time = _NOW - FAILED_STALE_AFTER - timedelta(days=1)
    snap = factory_crow_snapshot(
        factory_crow_session(status="failed", last_seen=stale_time, ticket_status="in_progress")
    )
    entries = entries_from_snapshot(snap, now=_NOW)
    assert len(entries) == 1


# ---------------------------------------------------------------------------
# CrowEntry health
# ---------------------------------------------------------------------------


def test_entry_health_running_not_stuck_is_green() -> None:
    recent = _NOW - timedelta(seconds=10)
    snap = factory_crow_snapshot(factory_crow_session(status="running", last_seen=recent))
    entries = entries_from_snapshot(snap, now=_NOW)
    assert entries[0].health == Health.GREEN


def test_entry_health_escalating_is_red() -> None:
    snap = factory_crow_snapshot(factory_crow_session(status="escalating"))
    entries = entries_from_snapshot(snap, now=_NOW)
    assert entries[0].health == Health.RED


def test_roster_store_identical_snapshot_does_not_notify() -> None:
    store = RosterStore()
    snap = factory_crow_snapshot(factory_crow_session())
    store.ingest_snapshot(snap, now=_NOW)

    notified: list[None] = []
    store.subscribe(lambda: notified.append(None))

    store.ingest_snapshot(snap, now=_NOW)
    assert notified == []


def test_roster_store_changed_snapshot_notifies() -> None:
    store = RosterStore()
    snap1 = factory_crow_snapshot(factory_crow_session())
    store.ingest_snapshot(snap1, now=_NOW)

    notified: list[None] = []
    store.subscribe(lambda: notified.append(None))

    snap2 = factory_crow_snapshot(factory_crow_session(agent_id="crow-t002"), key="k2")
    store.ingest_snapshot(snap2, now=_NOW)
    assert len(notified) == 1


def test_roster_store_no_textual_import() -> None:
    # The roster store is the framework-free projection seam: it must stay
    # importable without textual so non-TUI frontends can reuse it.
    source = pathlib.Path(roster_mod.__file__).read_text()
    assert not re.search(r"^\s*(from|import)\s+textual", source, re.MULTILINE)
