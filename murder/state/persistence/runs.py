"""Persistence for the runs table."""

from __future__ import annotations

from datetime import datetime

from murder.state.persistence.connection import RepoDb


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def insert_run(db: RepoDb, run_id: str, config_snapshot: str) -> None:
    db.conn.execute(
        "INSERT INTO runs(repository_id, run_id, started_at, config_snapshot) VALUES (?, ?, ?, ?)",
        (db.repository_id, run_id, _now(), config_snapshot),
    )


def set_run_advanced_log_path(db: RepoDb, run_id: str, path: str) -> None:
    """Store the advanced flight-recorder DB pointer on the run row (Phase 2).

    The main DB stores ONLY this pointer, never the bulky records.
    """
    db.conn.execute(
        "UPDATE runs SET advanced_log_path = ? WHERE repository_id = ? AND run_id = ?",
        (path, db.repository_id, run_id),
    )


def end_run(db: RepoDb, run_id: str) -> None:
    db.conn.execute(
        "UPDATE runs SET ended_at = ? WHERE repository_id = ? AND run_id = ?",
        (_now(), db.repository_id, run_id),
    )
