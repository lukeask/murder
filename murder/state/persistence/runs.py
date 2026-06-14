"""Persistence for the runs table."""

from __future__ import annotations

import sqlite3
from datetime import datetime


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def insert_run(conn: sqlite3.Connection, run_id: str, config_snapshot: str) -> None:
    conn.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot) VALUES (?, ?, ?)",
        (run_id, _now(), config_snapshot),
    )


def set_run_advanced_log_path(conn: sqlite3.Connection, run_id: str, path: str) -> None:
    """Store the advanced flight-recorder DB pointer on the run row (Phase 2).

    The main DB stores ONLY this pointer, never the bulky records.
    """
    conn.execute(
        "UPDATE runs SET advanced_log_path = ? WHERE run_id = ?",
        (path, run_id),
    )


def end_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute("UPDATE runs SET ended_at = ? WHERE run_id = ?", (_now(), run_id))
