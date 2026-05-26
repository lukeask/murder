"""Persistence helpers for check_results and completion_attempts tables."""

from __future__ import annotations

import sqlite3


def write_check_result(
    conn: sqlite3.Connection,
    ticket_id: str,
    check_name: str,
    timestamp: str,
    status: str,
    data_json: str | None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO check_results"
        " (ticket_id, check_name, timestamp, status, data_json)"
        " VALUES (?, ?, ?, ?, ?)",
        (ticket_id, check_name, timestamp, status, data_json),
    )
    conn.commit()


def get_attempts(conn: sqlite3.Connection, ticket_id: str, check_name: str) -> int:
    row = conn.execute(
        "SELECT attempts FROM completion_attempts WHERE ticket_id = ? AND check_name = ?",
        (ticket_id, check_name),
    ).fetchone()
    return int(row["attempts"]) if row else 0


def bump_attempts(conn: sqlite3.Connection, ticket_id: str, check_name: str) -> None:
    conn.execute(
        "INSERT INTO completion_attempts (ticket_id, check_name, attempts) VALUES (?, ?, 1)"
        " ON CONFLICT (ticket_id, check_name) DO UPDATE SET attempts = attempts + 1",
        (ticket_id, check_name),
    )
    conn.commit()


def reset_attempts(conn: sqlite3.Connection, ticket_id: str, check_name: str) -> None:
    conn.execute(
        "INSERT INTO completion_attempts (ticket_id, check_name, attempts) VALUES (?, ?, 0)"
        " ON CONFLICT (ticket_id, check_name) DO UPDATE SET attempts = 0",
        (ticket_id, check_name),
    )
    conn.commit()


__all__ = [
    "bump_attempts",
    "get_attempts",
    "reset_attempts",
    "write_check_result",
]
