"""Server-side roster filtering in the crow snapshot read model.

Ported from the Textual roster predicates (`app/tui/stores/roster.py`): the wire
roster must exclude done/dead agents (handled in SQL) and stale failed agents,
so the Ink TUI — which does no client-side filtering — never shows them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from murder.app.service.read_model import FAILED_STALE_AFTER, ServiceReadModel
from murder.state.persistence.agents import upsert_agent
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.paths import db_path


def _insert_ticket(conn, ticket_id: str, status: str) -> None:
    ts = datetime.utcnow().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO tickets (id, title, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (ticket_id, f"title-{ticket_id}", status, ts, ts),
    )


def _age_heartbeat(conn, agent_id: str, when: datetime) -> None:
    conn.execute(
        "UPDATE agents SET last_heartbeat_at = ? WHERE agent_id = ?",
        (when.isoformat(timespec="seconds"), agent_id),
    )


def _snapshot_ids(repo_root) -> set[str]:
    snapshot = ServiceReadModel(db_path(repo_root)).get_crow_snapshot()
    return {s.agent_id for s in snapshot.sessions}


def test_crow_snapshot_excludes_done_and_dead(repo_root) -> None:
    conn = get_db(db_path(repo_root))
    init_db(conn)
    upsert_agent(conn, agent_id="live", role="crow", ticket_id=None, session=None, status="running")
    upsert_agent(conn, agent_id="done", role="crow", ticket_id=None, session=None, status="done")
    upsert_agent(conn, agent_id="dead", role="crow", ticket_id=None, session=None, status="dead")
    conn.commit()
    conn.close()

    assert _snapshot_ids(repo_root) == {"live"}


def test_crow_snapshot_drops_stale_failed_on_terminal_ticket(repo_root) -> None:
    conn = get_db(db_path(repo_root))
    init_db(conn)
    _insert_ticket(conn, "t-done", "done")
    upsert_agent(
        conn, agent_id="stale", role="crow", ticket_id="t-done", session=None, status="failed"
    )
    _age_heartbeat(conn, "stale", datetime.utcnow() - FAILED_STALE_AFTER - timedelta(minutes=5))
    conn.commit()
    conn.close()

    assert _snapshot_ids(repo_root) == set()


def test_crow_snapshot_keeps_recent_failed(repo_root) -> None:
    conn = get_db(db_path(repo_root))
    init_db(conn)
    _insert_ticket(conn, "t-failed", "failed")
    upsert_agent(
        conn, agent_id="recent", role="crow", ticket_id="t-failed", session=None, status="failed"
    )
    _age_heartbeat(conn, "recent", datetime.utcnow() - timedelta(minutes=5))
    conn.commit()
    conn.close()

    assert _snapshot_ids(repo_root) == {"recent"}


def test_crow_snapshot_keeps_stale_failed_on_active_ticket(repo_root) -> None:
    # A failed agent whose ticket is still active stays even when stale: the
    # work item is not closed, so it remains actionable.
    conn = get_db(db_path(repo_root))
    init_db(conn)
    _insert_ticket(conn, "t-active", "in_progress")
    upsert_agent(
        conn, agent_id="active", role="crow", ticket_id="t-active", session=None, status="failed"
    )
    _age_heartbeat(conn, "active", datetime.utcnow() - FAILED_STALE_AFTER - timedelta(minutes=5))
    conn.commit()
    conn.close()

    assert _snapshot_ids(repo_root) == {"active"}


def test_crow_snapshot_drops_stale_failed_without_ticket(repo_root) -> None:
    # No ticket → empty ticket_status → droppable once stale (Textual semantics).
    conn = get_db(db_path(repo_root))
    init_db(conn)
    upsert_agent(
        conn, agent_id="orphan", role="crow", ticket_id=None, session=None, status="failed"
    )
    _age_heartbeat(conn, "orphan", datetime.utcnow() - FAILED_STALE_AFTER - timedelta(minutes=5))
    conn.commit()
    conn.close()

    assert _snapshot_ids(repo_root) == set()
