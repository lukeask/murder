"""Persistence helpers for check_results and completion_attempts tables."""

from __future__ import annotations

from murder.state.persistence.connection import RepoDb


def write_check_result(
    db: RepoDb,
    ticket_id: str,
    check_name: str,
    timestamp: str,
    status: str,
    data_json: str | None,
) -> None:
    db.conn.execute(
        "INSERT OR REPLACE INTO check_results"
        " (repository_id, ticket_id, check_name, timestamp, status, data_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (db.repository_id, ticket_id, check_name, timestamp, status, data_json),
    )
    db.conn.commit()


def get_attempts(db: RepoDb, ticket_id: str, check_name: str) -> int:
    row = db.conn.execute(
        "SELECT attempts FROM completion_attempts WHERE repository_id = ? AND ticket_id = ? AND check_name = ?",
        (db.repository_id, ticket_id, check_name),
    ).fetchone()
    return int(row["attempts"]) if row else 0


def bump_attempts(db: RepoDb, ticket_id: str, check_name: str) -> None:
    db.conn.execute(
        "INSERT INTO completion_attempts (repository_id, ticket_id, check_name, attempts) VALUES (?, ?, ?, 1)"
        " ON CONFLICT (repository_id, ticket_id, check_name) DO UPDATE SET attempts = attempts + 1",
        (db.repository_id, ticket_id, check_name),
    )
    db.conn.commit()


def get_latest_check_status(
    db: RepoDb, ticket_id: str, check_name: str
) -> str | None:
    row = db.conn.execute(
        """
        SELECT status FROM check_results
         WHERE repository_id = ? AND ticket_id = ? AND check_name = ?
         ORDER BY timestamp DESC
         LIMIT 1
        """,
        (db.repository_id, ticket_id, check_name),
    ).fetchone()
    return str(row["status"]) if row else None


def reset_attempts(db: RepoDb, ticket_id: str, check_name: str) -> None:
    db.conn.execute(
        "INSERT INTO completion_attempts (repository_id, ticket_id, check_name, attempts) VALUES (?, ?, ?, 0)"
        " ON CONFLICT (repository_id, ticket_id, check_name) DO UPDATE SET attempts = 0",
        (db.repository_id, ticket_id, check_name),
    )
    db.conn.commit()


__all__ = [
    "bump_attempts",
    "get_attempts",
    "get_latest_check_status",
    "reset_attempts",
    "write_check_result",
]
