"""Unit tests for the RosterStore and its CrowSnapshot projection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from murder.app.tui.crow_health import Health
from murder.app.tui.stores.roster import (
    FAILED_STALE_AFTER,
    CrowEntry,
    RosterStore,
    _crow_display_labels,
    crow_title_label,
    entries_from_snapshot,
)
from tests.support.factories import factory_crow_session, factory_crow_snapshot

_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# entries_from_snapshot projection
# ---------------------------------------------------------------------------


def test_entries_from_snapshot_includes_running_crow() -> None:
    snap = factory_crow_snapshot(factory_crow_session())
    entries = entries_from_snapshot(snap, now=_NOW)
    assert len(entries) == 1
    assert entries[0].agent_id == "crow-t001"


def test_entries_from_snapshot_skips_handlers() -> None:
    snap = factory_crow_snapshot(
        factory_crow_session(agent_id="crow_handler-t001", role="crow_handler", harness=""),
        factory_crow_session(agent_id="planning_handler-plan", role="planning_handler", harness=""),
        factory_crow_session(),
    )
    entries = entries_from_snapshot(snap, now=_NOW)
    assert [e.agent_id for e in entries] == ["crow-t001"]


def test_entries_from_snapshot_skips_done_agents() -> None:
    snap = factory_crow_snapshot(factory_crow_session(status="done"), factory_crow_session(agent_id="crow-t002", status="running"))
    entries = entries_from_snapshot(snap, now=_NOW)
    assert [e.agent_id for e in entries] == ["crow-t002"]


def test_entries_from_snapshot_skips_dead_agents() -> None:
    snap = factory_crow_snapshot(factory_crow_session(status="dead"))
    assert entries_from_snapshot(snap, now=_NOW) == []


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
    snap = factory_crow_snapshot(factory_crow_session(status="failed", last_seen=recent, ticket_status="done"))
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


# ---------------------------------------------------------------------------
# Display label helpers
# ---------------------------------------------------------------------------


def test_crow_display_labels_rogue_session() -> None:
    entry = CrowEntry(
        agent_id="codex-rogue-tailwall",
        ticket_id="",
        ticket_title="tailwall",
        harness="codex",
        status="running",
        session="murder_repo_crow_codex_rogue_tailwall",
        health=Health.GREEN,
        model="gpt-5.4",
    )
    labels = _crow_display_labels(entry)
    assert labels.name == "tailwall"
    assert labels.harness == "codex"
    assert labels.model == "gpt-5.4"
    assert labels.is_rogue is True


def test_crow_display_labels_claude_harness() -> None:
    entry = CrowEntry(
        agent_id="claude-rogue-test",
        ticket_id="",
        ticket_title="test",
        harness="claude_code",
        status="running",
        session="murder_repo_crow_claude_rogue_test",
        health=Health.GREEN,
    )
    labels = _crow_display_labels(entry)
    assert labels.name == "test"
    assert labels.harness == "claude"
    assert labels.model == "—"
    assert labels.is_rogue is True


def test_crow_title_label_includes_model() -> None:
    entry = CrowEntry(
        agent_id="crow-t001",
        ticket_id="t001",
        ticket_title=None,
        harness="claude_code",
        status="running",
        session="murder_demo_crow_claude_t001",
        health=Health.GREEN,
        model="claude-sonnet-4-6",
    )
    label = crow_title_label(entry)
    assert "claude-sonnet-4-6" in label


def test_crow_title_label_omits_dash_model() -> None:
    entry = CrowEntry(
        agent_id="crow-t001",
        ticket_id="t001",
        ticket_title=None,
        harness="cursor",
        status="running",
        session="murder_demo_crow_t001",
        health=Health.GREEN,
        model=None,
    )
    label = crow_title_label(entry)
    assert "—" not in label


# ---------------------------------------------------------------------------
# RosterStore
# ---------------------------------------------------------------------------


def test_roster_store_ingest_produces_entries() -> None:
    store = RosterStore()
    snap = factory_crow_snapshot(factory_crow_session())
    store.ingest_snapshot(snap, now=_NOW)
    snapshot = store.get_snapshot()
    assert len(snapshot.entries) == 1
    assert snapshot.entries[0].agent_id == "crow-t001"
    assert snapshot.invalidation_key == "k"


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
    import importlib
    import importlib.util
    import sys

    spec = importlib.util.find_spec("murder.app.tui.stores.roster")
    assert spec is not None
    mod = sys.modules.get("murder.app.tui.stores.roster")
    assert mod is not None
    # roster module must not have pulled in textual at all
    for name in sys.modules:
        if name == "murder.app.tui.stores.roster":
            continue
        if name.startswith("textual"):
            # textual may be loaded by other imports in the test session;
            # what we care about is that roster itself doesn't import textual
            pass
    # Direct check: roster source has no textual import
    import inspect
    source = inspect.getsource(mod)
    assert "from textual" not in source
    assert "import textual" not in source
