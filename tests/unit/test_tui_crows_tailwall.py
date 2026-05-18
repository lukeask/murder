"""Tests for the Crows tail-wall: health classifier + DB projection."""

from __future__ import annotations

import inspect
import sqlite3
from datetime import datetime, timezone

from murder.tui.crow_health import HEALTH_BORDER_COLOR, Health, classify
from murder.tui.crows_view import CrowEntry, CrowsView, load_crow_entries


def _insert_ticket(
    db: sqlite3.Connection,
    ticket_id: str,
    *,
    title: str = "x",
    status: str = "in_progress",
) -> None:
    db.execute(
        "INSERT INTO tickets(id, title, wave, status, created_at, updated_at) "
        "VALUES (?, ?, 0, ?, '2026-05-14T00:00:00', '2026-05-14T00:00:00')",
        (ticket_id, title, status),
    )


def _insert_agent(
    db: sqlite3.Connection,
    *,
    agent_id: str,
    role: str = "crow",
    ticket_id: str | None,
    status: str = "running",
    session: str | None = "tmux-1",
    started_at: str = "2026-05-14T01:00:00",
    last_heartbeat_at: str | None = None,
) -> None:
    db.execute(
        """
        INSERT INTO agents(
            agent_id, role, ticket_id, session, status, started_at, last_heartbeat_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (agent_id, role, ticket_id, session, status, started_at, last_heartbeat_at),
    )


def _insert_escalation(
    db: sqlite3.Connection,
    *,
    ticket_id: str,
    severity: int = 2,
    resolved: int = 0,
) -> None:
    db.execute(
        "INSERT INTO escalations(ts, ticket_id, severity, reason, to_recipient, resolved) "
        "VALUES ('2026-05-14T02:00:00', ?, ?, 'why', 'user', ?)",
        (ticket_id, severity, resolved),
    )


# ── crow_health.classify ──────────────────────────────────────────────────


def test_classify_red_takes_priority_over_status() -> None:
    assert classify(status="running", open_escalations=1) is Health.RED


def test_classify_blocked_status_is_red() -> None:
    assert classify(status="blocked") is Health.RED


def test_classify_running_with_no_signals_is_green() -> None:
    assert classify(status="running") is Health.GREEN


def test_classify_stuck_overrides_green_to_yellow() -> None:
    assert classify(status="running", stuck=True) is Health.YELLOW


def test_classify_stuck_does_not_override_red() -> None:
    assert classify(status="running", open_escalations=1, stuck=True) is Health.RED


def test_classify_done_is_neutral_not_green() -> None:
    assert classify(status="done") is Health.NEUTRAL


def test_classify_unknown_status_is_neutral() -> None:
    assert classify(status=None) is Health.NEUTRAL
    assert classify(status="") is Health.NEUTRAL


def test_border_colors_cover_every_health_value() -> None:
    for h in Health:
        assert h in HEALTH_BORDER_COLOR


# ── load_crow_entries ─────────────────────────────────────────────────────


def test_load_crow_entries_returns_empty_when_no_agents(memdb: sqlite3.Connection) -> None:
    assert load_crow_entries(memdb) == []


def test_load_crow_entries_skips_done_and_dead(memdb: sqlite3.Connection) -> None:
    _insert_ticket(memdb, "t1")
    _insert_ticket(memdb, "t2")
    _insert_ticket(memdb, "t3")
    _insert_agent(memdb, agent_id="a-live", ticket_id="t1", status="running")
    _insert_agent(memdb, agent_id="a-done", ticket_id="t2", status="done")
    _insert_agent(memdb, agent_id="a-dead", ticket_id="t3", status="dead")

    entries = load_crow_entries(memdb)
    assert [e.agent_id for e in entries] == ["a-live"]


def test_load_crow_entries_orders_attention_first(memdb: sqlite3.Connection) -> None:
    for tid in ("ta", "tb", "tc", "td"):
        _insert_ticket(memdb, tid)
    _insert_agent(memdb, agent_id="a-running", ticket_id="ta", status="running")
    _insert_agent(memdb, agent_id="a-blocked", ticket_id="tb", status="blocked")
    _insert_agent(memdb, agent_id="a-escal", ticket_id="tc", status="escalating")
    _insert_agent(memdb, agent_id="a-idle", ticket_id="td", status="idle")

    entries = load_crow_entries(memdb)
    assert [e.agent_id for e in entries] == [
        "a-escal",
        "a-blocked",
        "a-running",
        "a-idle",
    ]


def test_load_crow_entries_classifies_open_escalation_as_red(memdb: sqlite3.Connection) -> None:
    _insert_ticket(memdb, "t1", title="ship feature")
    _insert_agent(memdb, agent_id="a1", ticket_id="t1", status="running")
    _insert_escalation(memdb, ticket_id="t1", severity=2, resolved=0)

    entries = load_crow_entries(memdb)
    assert len(entries) == 1
    assert entries[0].health is Health.RED
    assert entries[0].ticket_title == "ship feature"


def test_load_crow_entries_ignores_resolved_escalations(memdb: sqlite3.Connection) -> None:
    _insert_ticket(memdb, "t1")
    _insert_agent(memdb, agent_id="a1", ticket_id="t1", status="running")
    _insert_escalation(memdb, ticket_id="t1", severity=3, resolved=1)

    entries = load_crow_entries(memdb)
    assert entries[0].health is Health.GREEN


def test_load_crow_entries_handles_null_ticket_link(memdb: sqlite3.Connection) -> None:
    """Notetaker/collaborator agents aren't tied to a ticket; the wall must
    not blow up on the LEFT JOIN."""
    _insert_agent(memdb, agent_id="notetaker-1", role="notetaker", ticket_id=None)
    entries = load_crow_entries(memdb)
    assert len(entries) == 1
    assert entries[0].ticket_id is None
    assert entries[0].ticket_title == ""


def test_back_to_wall_uses_saved_id_before_clearing() -> None:
    """Regression: `action_back_to_wall` must read `enlarged_agent_id`
    *before* it sets it to None, or it can never find the previously-
    enlarged tile to restore focus to."""
    src = inspect.getsource(CrowsView.action_back_to_wall)
    clear_idx = src.index("self.enlarged_agent_id = None")
    snapshot_idx = src.index("= self.enlarged_agent_id")
    assert snapshot_idx < clear_idx, (
        "action_back_to_wall must snapshot enlarged_agent_id before clearing it"
    )


def test_load_crow_entries_produces_crow_entry_dataclass(memdb: sqlite3.Connection) -> None:
    _insert_ticket(memdb, "t1", title="t1 title")
    _insert_agent(
        memdb,
        agent_id="a1",
        ticket_id="t1",
        status="running",
        session="tmux-foo",
    )
    [entry] = load_crow_entries(memdb)
    assert isinstance(entry, CrowEntry)
    assert entry.session == "tmux-foo"
    assert entry.role == "crow"


def test_load_crow_entries_hides_stale_failed_terminal_ticket(memdb: sqlite3.Connection) -> None:
    now = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
    _insert_ticket(memdb, "t-stale", status="failed")
    _insert_ticket(memdb, "t-live", status="in_progress")
    _insert_agent(
        memdb,
        agent_id="a-stale",
        ticket_id="t-stale",
        status="failed",
        started_at="2026-05-13T07:00:00",
        last_heartbeat_at="2026-05-13T07:01:00",
    )
    _insert_agent(memdb, agent_id="a-live", ticket_id="t-live", status="running")

    entries = load_crow_entries(memdb, now=now)
    assert [e.agent_id for e in entries] == ["a-live"]


def test_load_crow_entries_keeps_recent_failed_terminal_ticket(memdb: sqlite3.Connection) -> None:
    now = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
    _insert_ticket(memdb, "t-recent", status="failed")
    _insert_agent(
        memdb,
        agent_id="a-recent-failed",
        ticket_id="t-recent",
        status="failed",
        started_at="2026-05-14T09:30:00",
        last_heartbeat_at="2026-05-14T09:59:00",
    )

    entries = load_crow_entries(memdb, now=now)
    assert [e.agent_id for e in entries] == ["a-recent-failed"]


def test_load_crow_entries_keeps_failed_for_active_ticket_even_if_old(
    memdb: sqlite3.Connection,
) -> None:
    now = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
    _insert_ticket(memdb, "t-active", status="in_progress")
    _insert_agent(
        memdb,
        agent_id="a-old-failed",
        ticket_id="t-active",
        status="failed",
        started_at="2026-05-13T01:00:00",
        last_heartbeat_at="2026-05-13T01:05:00",
    )

    entries = load_crow_entries(memdb, now=now)
    assert [e.agent_id for e in entries] == ["a-old-failed"]
