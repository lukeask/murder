"""Persistence for the commands and worker_heartbeats tables."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from murder.state.persistence.event_log import insert_event
from murder.state.persistence.records import CommandRecord, command_record_from_row


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def enqueue_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    run_id: str,
    agent_id: str,
    role: str | None,
    ticket_id: str | None,
    target_worker: str,
    kind: str,
    payload: dict[str, Any],
    correlation_id: str,
    idempotency_key: str,
    status: str = "pending",
    claimed_by: str | None = None,
    lease_expires_at: int | None = None,
    attempt_count: int = 0,
    retryable: bool = True,
    result: dict[str, Any] | None = None,
    last_error: str | None = None,
) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO commands
            (id, created_at, updated_at, run_id, agent_id, role, ticket_id, target_worker,
             kind, payload_json, correlation_id, idempotency_key, status, claimed_by,
             lease_expires_at, attempt_count, retryable, result_json, last_error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            command_id,
            now,
            now,
            run_id,
            agent_id,
            role,
            ticket_id,
            target_worker,
            kind,
            json.dumps(payload, default=str),
            correlation_id,
            idempotency_key,
            status,
            claimed_by,
            lease_expires_at,
            attempt_count,
            1 if retryable else 0,
            json.dumps(result, default=str) if result is not None else None,
            last_error,
        ),
    )


def claim_next_command(
    conn: sqlite3.Connection,
    *,
    target_worker: str,
    claimed_by: str,
    lease_expires_at: int,
) -> CommandRecord | None:
    row = conn.execute(
        """
        SELECT id
          FROM commands
         WHERE target_worker = ?
           AND status = 'pending'
         ORDER BY created_at, id
         LIMIT 1
        """,
        (target_worker,),
    ).fetchone()
    if row is None:
        return None
    conn.execute(
        """
        UPDATE commands
           SET status = 'in_flight',
               claimed_by = ?,
               lease_expires_at = ?,
               attempt_count = attempt_count + 1,
               updated_at = ?
         WHERE id = ?
        """,
        (claimed_by, lease_expires_at, _now(), row["id"]),
    )
    claimed = conn.execute("SELECT * FROM commands WHERE id = ?", (row["id"],)).fetchone()
    return command_record_from_row(claimed) if claimed else None


def complete_command(
    conn: sqlite3.Connection, *, command_id: str, result: dict[str, Any] | None = None
) -> None:
    conn.execute(
        """
        UPDATE commands
           SET status = 'done',
               result_json = ?,
               updated_at = ?
         WHERE id = ?
        """,
        (json.dumps(result, default=str) if result is not None else None, _now(), command_id),
    )


def fail_command(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    last_error: str,
    retryable: bool = True,
) -> None:
    conn.execute(
        """
        UPDATE commands
           SET status = 'failed',
               retryable = ?,
               last_error = ?,
               updated_at = ?
         WHERE id = ?
        """,
        (1 if retryable else 0, last_error, _now(), command_id),
    )


def reap_stale_commands(
    conn: sqlite3.Connection,
    *,
    now_epoch: int,
    max_attempts: int = 3,
) -> dict[str, list[str]]:
    """Reclaim expired in-flight commands.

    Retryable commands go back to ``pending`` until ``max_attempts`` is
    reached. Exhausted or non-retryable commands become ``failed``; the
    supervisor is responsible for emitting escalation events for returned
    ``failed`` ids.
    """
    rows = conn.execute(
        """
        SELECT id, retryable, attempt_count
          FROM commands
         WHERE status = 'in_flight'
           AND lease_expires_at IS NOT NULL
           AND lease_expires_at <= ?
         ORDER BY updated_at, id
        """,
        (now_epoch,),
    ).fetchall()
    retried: list[str] = []
    failed: list[str] = []
    now = _now()
    for row in rows:
        command_id = str(row["id"])
        # attempt_count already counts attempts made: claim_next_command does
        # attempt_count + 1 at lease time, so a command with attempt_count == N
        # has been dispatched N times. Re-pending leaves the count untouched and
        # lets the next claim increment it; comparing attempts-used against
        # max_attempts here (rather than a pre-incremented value) is what allows
        # the full max_attempts dispatches instead of one fewer.
        attempts_used = int(row["attempt_count"] or 0)
        if int(row["retryable"] or 0) == 1 and attempts_used < max_attempts:
            conn.execute(
                """
                UPDATE commands
                   SET status = 'pending',
                       claimed_by = NULL,
                       lease_expires_at = NULL,
                       updated_at = ?
                 WHERE id = ?
                """,
                (now, command_id),
            )
            retried.append(command_id)
            continue
        conn.execute(
            """
            UPDATE commands
               SET status = 'failed',
                   claimed_by = NULL,
                   lease_expires_at = NULL,
                   last_error = COALESCE(last_error, 'command lease expired'),
                   updated_at = ?
             WHERE id = ?
            """,
            (now, command_id),
        )
        failed.append(command_id)
    return {"retried": retried, "failed": failed}


def upsert_worker_heartbeat(
    conn: sqlite3.Connection,
    *,
    worker_id: str,
    run_id: str,
    role: str | None = None,
    ticket_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    now = _now()
    payload_json = json.dumps(payload or {}, default=str)
    conn.execute(
        """
        INSERT INTO worker_heartbeats(
            worker_id, run_id, role, ticket_id, last_heartbeat_at, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(worker_id) DO UPDATE SET
            run_id = excluded.run_id,
            role = excluded.role,
            ticket_id = excluded.ticket_id,
            last_heartbeat_at = excluded.last_heartbeat_at,
            payload_json = excluded.payload_json
        """,
        (worker_id, run_id, role, ticket_id, now, payload_json),
    )


def get_command_status(conn: sqlite3.Connection, command_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT status, result_json, last_error, updated_at FROM commands WHERE id = ?",
        (command_id,),
    ).fetchone()
    return dict(row) if row else None


def get_worker_heartbeat(conn: sqlite3.Connection, worker_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM worker_heartbeats WHERE worker_id = ?",
        (worker_id,),
    ).fetchone()
    return dict(row) if row else None


def insert_command_event(
    conn: sqlite3.Connection,
    *,
    command_id: str,
    run_id: str,
    agent_id: str,
    role: str | None,
    ticket_id: str | None,
    target_worker: str,
    kind: str,
    payload: dict[str, Any],
    correlation_id: str,
    idempotency_key: str,
    status: str,
    claimed_by: str | None,
    lease_expires_at: int | None,
    attempt_count: int,
    retryable: bool,
    result: dict[str, Any] | None,
    event_type: str,
    event_payload: dict[str, Any],
    ts: str | None = None,
    schema_version: int = 1,
) -> int:
    conn.execute("BEGIN")
    try:
        enqueue_command(
            conn,
            command_id=command_id,
            run_id=run_id,
            agent_id=agent_id,
            role=role,
            ticket_id=ticket_id,
            target_worker=target_worker,
            kind=kind,
            payload=payload,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            status=status,
            claimed_by=claimed_by,
            lease_expires_at=lease_expires_at,
            attempt_count=attempt_count,
            retryable=retryable,
            result=result,
        )
        event_id = insert_event(
            conn,
            run_id=run_id,
            agent_id=agent_id,
            role=role or "",
            ticket_id=ticket_id,
            type=event_type,
            payload=event_payload,
            schema_version=schema_version,
            ts=ts,
        )
        conn.execute("COMMIT")
        return event_id
    except Exception:
        conn.execute("ROLLBACK")
        raise
