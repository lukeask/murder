from __future__ import annotations

from pathlib import Path

from murder.roster import RosterService
from murder.state.persistence.agents import upsert_agent
from murder.state.persistence.connection import RepoDb
from tests.support.database import open_test_repo_db


def _db(repo_root: Path) -> RepoDb:
    database = repo_root / ".murder" / "murder.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    return open_test_repo_db(database)


def test_agents_persist_worktree_path(repo_root: Path) -> None:
    db = _db(repo_root)

    upsert_agent(
        db,
        agent_id="crow-t001",
        role="crow",
        ticket_id=None,
        session="murder_repo_crow_t001",
        status="running",
        worktree_path=str(repo_root / ".murder" / "worktrees" / "crow" / "t001"),
    )

    row = db.conn.execute(
        "SELECT worktree_path FROM agents WHERE repository_id = ? AND agent_id = ?",
        (db.repository_id, "crow-t001"),
    ).fetchone()
    assert row["worktree_path"].endswith(".murder/worktrees/crow/t001")


def test_agent_upsert_preserves_existing_worktree_path_when_omitted(repo_root: Path) -> None:
    db = _db(repo_root)
    worktree_path = str(repo_root / ".murder" / "worktrees" / "crow" / "t001")

    upsert_agent(
        db,
        agent_id="crow-t001",
        role="crow",
        ticket_id=None,
        session="murder_repo_crow_t001",
        status="running",
        worktree_path=worktree_path,
    )
    upsert_agent(
        db,
        agent_id="crow-t001",
        role="crow",
        ticket_id=None,
        session="murder_repo_crow_t001",
        status="done",
    )

    row = db.conn.execute(
        "SELECT worktree_path FROM agents WHERE repository_id = ? AND agent_id = ?",
        (db.repository_id, "crow-t001"),
    ).fetchone()
    assert row["worktree_path"] == worktree_path


def test_crow_snapshot_exposes_worktree_path(repo_root: Path) -> None:
    db = _db(repo_root)
    db.conn.execute(
        """
        INSERT INTO tickets(repository_id, id, title, status, created_at, updated_at)
        VALUES (?, 't001', 'Fix thing', 'in_progress', '2026-01-01', '2026-01-01')
        """,
        (db.repository_id,),
    )
    worktree_path = repo_root / ".murder" / "worktrees" / "crow" / "t001"
    upsert_agent(
        db,
        agent_id="crow-t001",
        role="crow",
        ticket_id="t001",
        session="murder_repo_crow_t001",
        status="running",
        worktree_path=str(worktree_path),
    )

    snapshot = RosterService(db).get()

    assert snapshot["sessions"][0]["worktree_path"] == str(worktree_path)


def test_rogue_crow_snapshot_exposes_agent_harness(repo_root: Path) -> None:
    db = _db(repo_root)

    upsert_agent(
        db,
        agent_id="claude-rogue-test",
        role="crow",
        ticket_id=None,
        session="murder_repo_crow_claude_rogue_test",
        harness="claude_code",
        status="running",
    )

    snapshot = RosterService(db).get()

    assert snapshot["sessions"][0]["harness"] == "claude_code"
