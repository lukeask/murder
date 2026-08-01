"""Server-side roster filtering in the crow snapshot read model.

Ported from the Textual roster predicates (`app/tui/stores/roster.py`): the wire
roster must exclude done/dead agents (handled in SQL) and stale failed agents,
so the Ink TUI — which does no client-side filtering — never shows them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from murder.app.service.read_model import FAILED_STALE_AFTER
from murder.roster import RosterService
from murder.state.persistence.agents import upsert_agent
from murder.state.persistence.connection import RepoDb


def _insert_ticket(db: RepoDb, ticket_id: str, status: str) -> None:
    db.conn.execute(
        "INSERT INTO tickets(repository_id, id, title, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, '2026-01-01', '2026-01-01')",
        (db.repository_id, ticket_id, f"title-{ticket_id}", status),
    )


def _age_heartbeat(db: RepoDb, agent_id: str, when: datetime) -> None:
    db.conn.execute(
        "UPDATE agents SET last_heartbeat_at = ? WHERE repository_id = ? AND agent_id = ?",
        (when.isoformat(timespec="seconds"), db.repository_id, agent_id),
    )


def _snapshot_ids(db: RepoDb) -> set[str]:
    snapshot = RosterService(db).get()
    return {str(session["agent_id"]) for session in snapshot["sessions"]}


def test_crow_snapshot_excludes_done_and_dead(repo_db: RepoDb) -> None:
    upsert_agent(
        repo_db, agent_id="live", role="crow", ticket_id=None, session=None, status="running"
    )
    upsert_agent(repo_db, agent_id="done", role="crow", ticket_id=None, session=None, status="done")
    upsert_agent(repo_db, agent_id="dead", role="crow", ticket_id=None, session=None, status="dead")

    assert _snapshot_ids(repo_db) == {"live"}


def test_crow_snapshot_drops_stale_failed_on_terminal_ticket(repo_db: RepoDb) -> None:
    _insert_ticket(repo_db, "t-done", "done")
    upsert_agent(
        repo_db, agent_id="stale", role="crow", ticket_id="t-done", session=None, status="failed"
    )
    _age_heartbeat(repo_db, "stale", datetime.utcnow() - FAILED_STALE_AFTER - timedelta(minutes=5))

    assert _snapshot_ids(repo_db) == set()


def test_crow_snapshot_keeps_recent_failed(repo_db: RepoDb) -> None:
    _insert_ticket(repo_db, "t-failed", "failed")
    upsert_agent(
        repo_db, agent_id="recent", role="crow", ticket_id="t-failed", session=None, status="failed"
    )
    _age_heartbeat(repo_db, "recent", datetime.utcnow() - timedelta(minutes=5))

    assert _snapshot_ids(repo_db) == {"recent"}


def test_crow_snapshot_keeps_stale_failed_on_active_ticket(repo_db: RepoDb) -> None:
    # A failed agent whose ticket is still active stays even when stale: the
    # work item is not closed, so it remains actionable.
    _insert_ticket(repo_db, "t-active", "in_progress")
    upsert_agent(
        repo_db, agent_id="active", role="crow", ticket_id="t-active", session=None, status="failed"
    )
    _age_heartbeat(repo_db, "active", datetime.utcnow() - FAILED_STALE_AFTER - timedelta(minutes=5))

    assert _snapshot_ids(repo_db) == {"active"}


def test_crow_snapshot_drops_stale_failed_without_ticket(repo_db: RepoDb) -> None:
    # No ticket → empty ticket_status → droppable once stale (Textual semantics).
    upsert_agent(
        repo_db, agent_id="orphan", role="crow", ticket_id=None, session=None, status="failed"
    )
    _age_heartbeat(repo_db, "orphan", datetime.utcnow() - FAILED_STALE_AFTER - timedelta(minutes=5))

    assert _snapshot_ids(repo_db) == set()
