"""Seed-row builders for tests (CONTRIBUTING §"Builders, not fixture files").

These replace the dozen-plus hand-rolled ``_insert_ticket`` / ``_insert_agent``
/ ``_make_conv`` helpers that each re-implemented the INSERT column list inline
and were already drifting (some set ``attempts``, some ``harness``/``model``,
some not). Keep the column lists here so a schema change touches one place.

All builders take an open ``sqlite3.Connection`` (already ``init_db``'d) and
return the primary key so call sites read naturally::

    from tests.support import factories

    factories.insert_ticket(conn, "t001", status="in_progress")
    factories.insert_agent(conn, "crow-t001", role="crow", ticket_id="t001")
"""

from __future__ import annotations

import sqlite3

from murder.state.persistence.conversation import upsert_conversation

_DEFAULT_TS = "2026-01-01T00:00:00"


def insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    title: str | None = None,
    status: str = "planned",
    harness: str | None = None,
    model: str | None = None,
    worktree: str | None = None,
    attempts: int = 0,
    last_error: str | None = None,
    created_at: str = _DEFAULT_TS,
    updated_at: str = _DEFAULT_TS,
) -> str:
    """Insert a row into ``tickets`` and return its id."""
    conn.execute(
        """
        INSERT INTO tickets(
            id, title, status, harness, model, worktree,
            attempts, last_error, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ticket_id,
            title if title is not None else f"Title {ticket_id}",
            status,
            harness,
            model,
            worktree,
            attempts,
            last_error,
            created_at,
            updated_at,
        ),
    )
    return ticket_id


def insert_agent(
    conn: sqlite3.Connection,
    agent_id: str,
    *,
    role: str = "crow",
    ticket_id: str | None = None,
    session: str | None = None,
    status: str = "running",
    harness: str | None = None,
    model: str | None = None,
    worktree_path: str | None = None,
    started_at: str = _DEFAULT_TS,
    last_heartbeat_at: str | None = None,
    pid: int | None = None,
) -> str:
    """Insert a row into ``agents`` and return its agent_id."""
    conn.execute(
        """
        INSERT INTO agents(
            agent_id, role, ticket_id, session, harness, model, worktree_path,
            status, started_at, last_heartbeat_at, pid
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            agent_id,
            role,
            ticket_id,
            session,
            harness,
            model,
            worktree_path,
            status,
            started_at,
            last_heartbeat_at,
            pid,
        ),
    )
    return agent_id


def make_conversation(
    conn: sqlite3.Connection,
    conversation_id: str = "conv-1",
    *,
    agent_id: str = "agent-1",
    harness: str | None = "cc",
    model: str | None = "opus",
    status: str = "in_progress",
) -> str:
    """Upsert a conversation metadata row and return its conversation_id."""
    upsert_conversation(
        conn,
        conversation_id=conversation_id,
        agent_id=agent_id,
        harness=harness,
        model=model,
        status=status,
    )
    return conversation_id
