"""Crash recovery for the one-time repository partition rebuild."""

from __future__ import annotations

from pathlib import Path

import pytest

from murder.state.persistence import migrations
from murder.state.persistence.connection import Connection, connect


def _legacy_db(path: Path) -> Connection:
    conn = connect(path)
    conn.execute(
        "CREATE TABLE tickets (id TEXT PRIMARY KEY, title TEXT NOT NULL, "
        "status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE agents (agent_id TEXT PRIMARY KEY, role TEXT NOT NULL, "
        "ticket_id TEXT, status TEXT NOT NULL, started_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO tickets VALUES ('t001', 'legacy', 'ready', 'now', 'now')"
    )
    conn.execute(
        "INSERT INTO agents VALUES ('crow-t001', 'crow', 't001', 'running', 'now')"
    )
    return conn


def test_partition_rebuild_rolls_back_renames_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = _legacy_db(tmp_path / "rollback.db")

    def fail_first_schema(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated process failure")

    monkeypatch.setattr(
        "murder.state.persistence.migrations.execute_script", fail_first_schema
    )
    with pytest.raises(RuntimeError, match="simulated process failure"):
        migrations._migrate_repository_partition(conn, "repo-a")

    names = {
        str(row["name"])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "tickets" in names
    assert "agents" in names
    assert not any(name.endswith("__pre_partition") for name in names)
    assert conn.execute("SELECT id FROM tickets").fetchone()[0] == "t001"
    conn.close()


def test_partition_rebuild_resumes_pretransaction_staged_tables(tmp_path: Path) -> None:
    conn = _legacy_db(tmp_path / "resume.db")
    # Reproduce an interruption in the old autocommit rename loop.
    conn.execute("ALTER TABLE tickets RENAME TO tickets__pre_partition")

    migrations._migrate_repository_partition(conn, "repo-a")

    names = {
        str(row["name"])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert not any(name.endswith("__pre_partition") for name in names)
    ticket = conn.execute(
        "SELECT repository_id, id, title FROM tickets WHERE id='t001'"
    ).fetchone()
    agent = conn.execute(
        "SELECT repository_id, agent_id FROM agents WHERE agent_id='crow-t001'"
    ).fetchone()
    assert tuple(ticket) == ("repo-a", "t001", "legacy")
    assert tuple(agent) == ("repo-a", "crow-t001")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


def test_v1_global_deterministic_keys_upgrade_without_data_loss(tmp_path: Path) -> None:
    conn = connect(tmp_path / "v1.db")
    conn.execute(
        """CREATE TABLE agents (
        agent_id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, role TEXT NOT NULL,
        ticket_id TEXT, session TEXT, harness TEXT, model TEXT, worktree_path TEXT,
        status TEXT NOT NULL, start_commit TEXT, started_at TEXT NOT NULL,
        last_heartbeat_at TEXT, pid INTEGER)"""
    )
    conn.execute(
        """CREATE TABLE worker_heartbeats (
        worker_id TEXT PRIMARY KEY, repository_id TEXT NOT NULL, run_id TEXT NOT NULL,
        role TEXT, ticket_id TEXT, last_heartbeat_at TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}')"""
    )
    conn.execute(
        """CREATE TABLE agent_messages (
        agent_id TEXT NOT NULL, ordinal INTEGER NOT NULL, role TEXT NOT NULL,
        body TEXT NOT NULL, captured_at TEXT NOT NULL, repository_id TEXT NOT NULL,
        PRIMARY KEY (agent_id, ordinal))"""
    )
    conn.execute(
        """CREATE TABLE conversations (
        conversation_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, harness TEXT, model TEXT,
        harness_session_id TEXT, live_state TEXT, queued_message TEXT, status TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, repository_id TEXT NOT NULL)"""
    )
    conn.execute(
        """CREATE TABLE conversation_blocks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
        sealed INTEGER NOT NULL, service_received_at TEXT NOT NULL,
        repository_id TEXT NOT NULL, UNIQUE(conversation_id, ordinal))"""
    )
    conn.execute(
        """CREATE TABLE conversation_chunk_summaries (
        summary_id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
        chunk_idx INTEGER NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL,
        repository_id TEXT NOT NULL, UNIQUE(conversation_id, chunk_idx))"""
    )
    conn.execute(
        """CREATE TABLE history_status (
        item_id TEXT PRIMARY KEY, status TEXT NOT NULL, status_note TEXT,
        updated_at TEXT NOT NULL, repository_id TEXT NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO agents(repository_id, agent_id, role, status, started_at) "
        "VALUES ('repo-a', 'crow-t001', 'crow', 'running', 'now')"
    )
    conn.execute(
        "INSERT INTO agent_messages VALUES "
        "('crow-t001', 0, 'user', 'hello', 'now', 'repo-a')"
    )
    conn.execute(
        "INSERT INTO conversations(conversation_id, agent_id, status, created_at, updated_at, "
        "repository_id) VALUES ('crow-t001', 'crow-t001', 'in_progress', 'now', 'now', 'repo-a')"
    )
    conn.execute(
        "INSERT INTO conversation_blocks(conversation_id, ordinal, kind, payload_json, sealed, "
        "service_received_at, repository_id) "
        "VALUES ('crow-t001', 0, 'user', '{}', 1, 'now', 'repo-a')"
    )
    conn.execute(
        "INSERT INTO history_status VALUES ('crow-t001:0', 'dismissed', NULL, 'now', 'repo-a')"
    )

    migrations._migrate_partition_local_deterministic_ids(conn)

    assert migrations._primary_key_columns(conn, "agents") == ("repository_id", "agent_id")
    assert migrations._primary_key_columns(conn, "worker_heartbeats") == (
        "repository_id",
        "worker_id",
    )
    assert migrations._primary_key_columns(conn, "agent_messages") == (
        "repository_id",
        "agent_id",
        "ordinal",
    )
    assert migrations._primary_key_columns(conn, "conversations") == (
        "repository_id",
        "conversation_id",
    )
    assert migrations._primary_key_columns(conn, "history_status") == (
        "repository_id",
        "item_id",
    )
    assert conn.execute("SELECT body FROM agent_messages").fetchone()[0] == "hello"
    assert conn.execute("SELECT payload_json FROM conversation_blocks").fetchone()[0] == "{}"
    conn.close()
