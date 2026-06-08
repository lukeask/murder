from __future__ import annotations

import sqlite3
from pathlib import Path

from murder.state.persistence.schema import get_db, init_db

TICKET_ATTEMPTS = 2


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }


def _index_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()
    }


def test_fresh_ticket_schema_drops_wave_and_skills_and_adds_worktree(tmp_path: Path) -> None:
    conn = get_db(tmp_path / "murder.db")
    init_db(conn)

    ticket_cols = _column_names(conn, "tickets")
    assert "wave" not in ticket_cols
    assert "worktree" in ticket_cols
    assert "ticket_skills" not in _table_names(conn)
    assert "idx_tickets_wave" not in _index_names(conn)


def test_existing_ticket_schema_migrates_wave_skills_and_worktree(tmp_path: Path) -> None:
    conn = get_db(tmp_path / "murder.db")
    conn.executescript(
        """
        CREATE TABLE tickets (
            id         TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            wave       INTEGER NOT NULL,
            status     TEXT NOT NULL CHECK (status IN
                       ('planned','ready','in_progress','blocked','done','failed')),
            harness    TEXT,
            model      TEXT,
            attempts   INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX idx_tickets_wave ON tickets(wave);
        CREATE TABLE ticket_skills (
            ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
            skill     TEXT NOT NULL,
            PRIMARY KEY (ticket_id, skill)
        );
        INSERT INTO tickets (
            id, title, wave, status, harness, model, attempts, created_at, updated_at
        )
        VALUES
            ('t001', 'First', 7, 'planned', 'codex', 'gpt-5', 2,
             '2026-06-07T00:00:00', '2026-06-07T00:00:01'),
            ('t002', 'Second', 8, 'done', NULL, NULL, 0,
             '2026-06-07T00:00:00', '2026-06-07T00:00:01');
        INSERT INTO ticket_skills(ticket_id, skill) VALUES ('t001', 'fake-skill');
        """
    )

    init_db(conn)

    ticket_cols = _column_names(conn, "tickets")
    assert "wave" not in ticket_cols
    assert "worktree" in ticket_cols
    assert "ticket_skills" not in _table_names(conn)
    assert "idx_tickets_wave" not in _index_names(conn)

    row = conn.execute("SELECT * FROM tickets WHERE id = 't001'").fetchone()
    assert row["title"] == "First"
    assert row["status"] == "planned"
    assert row["harness"] == "codex"
    assert row["model"] == "gpt-5"
    assert row["worktree"] is None
    assert row["attempts"] == TICKET_ATTEMPTS

    conn.execute("INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES ('t001', 't002')")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
