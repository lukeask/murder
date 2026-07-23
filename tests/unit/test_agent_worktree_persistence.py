from __future__ import annotations

from pathlib import Path

from murder.state.persistence.agents import upsert_agent
from murder.state.persistence.schema import get_db, init_db
from murder.roster import RosterService


def test_agents_persist_worktree_path(repo_root: Path) -> None:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)

    upsert_agent(
        conn,
        agent_id="crow-t001",
        role="crow",
        ticket_id=None,
        session="murder_repo_crow_t001",
        status="running",
        worktree_path=str(repo_root / ".murder" / "worktrees" / "crow" / "t001"),
    )

    row = conn.execute(
        "SELECT worktree_path FROM agents WHERE agent_id = 'crow-t001'"
    ).fetchone()
    assert row["worktree_path"].endswith(".murder/worktrees/crow/t001")


def test_agent_upsert_preserves_existing_worktree_path_when_omitted(repo_root: Path) -> None:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    worktree_path = str(repo_root / ".murder" / "worktrees" / "crow" / "t001")

    upsert_agent(
        conn,
        agent_id="crow-t001",
        role="crow",
        ticket_id=None,
        session="murder_repo_crow_t001",
        status="running",
        worktree_path=worktree_path,
    )
    upsert_agent(
        conn,
        agent_id="crow-t001",
        role="crow",
        ticket_id=None,
        session="murder_repo_crow_t001",
        status="done",
    )

    row = conn.execute(
        "SELECT worktree_path FROM agents WHERE agent_id = 'crow-t001'"
    ).fetchone()
    assert row["worktree_path"] == worktree_path


def test_crow_snapshot_exposes_worktree_path(repo_root: Path) -> None:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    conn.execute(
        """
        INSERT INTO tickets(id, title, status, created_at, updated_at)
        VALUES ('t001', 'Fix thing', 'in_progress', '2026-01-01', '2026-01-01')
        """
    )
    worktree_path = repo_root / ".murder" / "worktrees" / "crow" / "t001"
    upsert_agent(
        conn,
        agent_id="crow-t001",
        role="crow",
        ticket_id="t001",
        session="murder_repo_crow_t001",
        status="running",
        worktree_path=str(worktree_path),
    )

    snapshot = RosterService(repo_root / ".murder" / "murder.db").get()

    assert snapshot["sessions"][0]["worktree_path"] == str(worktree_path)


def test_rogue_crow_snapshot_exposes_agent_harness(repo_root: Path) -> None:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)

    upsert_agent(
        conn,
        agent_id="claude-rogue-test",
        role="crow",
        ticket_id=None,
        session="murder_repo_crow_claude_rogue_test",
        harness="claude_code",
        status="running",
    )

    snapshot = RosterService(repo_root / ".murder" / "murder.db").get()

    assert snapshot["sessions"][0]["harness"] == "claude_code"
