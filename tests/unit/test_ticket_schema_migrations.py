from __future__ import annotations

import sqlite3
from pathlib import Path

from murder.state.persistence.migrations import _migrate_plans_single_master
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


# --- plans single-master FK rewrite + dangling-FK repair ---------------------

_LEGACY_PLANS_DDL = """
CREATE TABLE plans (
    name              TEXT PRIMARY KEY,
    status            TEXT NOT NULL CHECK (status IN ('draft','accepted','superseded')),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    body              TEXT NOT NULL,
    frontmatter_json  TEXT NOT NULL DEFAULT '{}',
    body_hash         TEXT NOT NULL,
    file_hash         TEXT,
    materialized_path TEXT NOT NULL,
    revision_count    INTEGER NOT NULL DEFAULT 0,
    sync_state        TEXT NOT NULL DEFAULT 'synced',
    parse_error       TEXT,
    conflict_reason   TEXT
);
"""


def _plan_revisions_fk_target(conn: sqlite3.Connection) -> str:
    return str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='plan_revisions'"
        ).fetchone()["sql"]
    )


def _plan_related_fk_target(conn: sqlite3.Connection) -> str:
    return str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='plan_related_tickets'"
        ).fetchone()["sql"]
    )


def test_plans_single_master_migration_does_not_corrupt_child_fks(tmp_path: Path) -> None:
    """Forward migration must not rewrite child FKs to the temp table."""
    conn = get_db(tmp_path / "murder.db")
    conn.executescript(
        _LEGACY_PLANS_DDL
        + """
        CREATE TABLE plan_revisions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_name        TEXT NOT NULL REFERENCES plans(name) ON DELETE CASCADE,
            created_at       TEXT NOT NULL,
            source           TEXT NOT NULL CHECK (source IN ('file','db','import')),
            status           TEXT NOT NULL,
            body             TEXT NOT NULL,
            frontmatter_json TEXT NOT NULL DEFAULT '{}',
            content_hash     TEXT NOT NULL
        );
        CREATE INDEX idx_plan_revisions_plan ON plan_revisions(plan_name, id);
        CREATE TABLE plan_related_tickets (
            plan_name TEXT NOT NULL REFERENCES plans(name) ON DELETE CASCADE,
            ticket_id TEXT NOT NULL,
            PRIMARY KEY (plan_name, ticket_id)
        );
        INSERT INTO plans
            (name, status, created_at, updated_at, body, body_hash,
             materialized_path, revision_count, sync_state, conflict_reason)
        VALUES
            ('p1', 'draft', '2026-06-07T00:00:00', '2026-06-07T00:00:01',
             'body', 'h', 'plans/p1.md', 0, 'synced', NULL);
        """
    )

    # Call the forward migration in ISOLATION — running full init_db would let
    # the repair migration heal any corruption in the same pass, masking a
    # regression in this fix. This asserts the forward migration itself is clean.
    _migrate_plans_single_master(conn)
    assert "plans_old_single_master_migration" not in _table_names(conn)

    # conflict_reason dropped → the forward migration actually ran.
    assert "conflict_reason" not in _column_names(conn, "plans")

    rev_sql = _plan_revisions_fk_target(conn)
    rel_sql = _plan_related_fk_target(conn)
    assert "plans_old_single_master_migration" not in rev_sql
    assert "plans_old_single_master_migration" not in rel_sql
    assert "REFERENCES plans(name)" in rev_sql
    assert "REFERENCES plans(name)" in rel_sql

    conn.execute(
        """
        INSERT INTO plan_revisions
            (plan_name, created_at, source, status, body, content_hash)
        VALUES ('p1', '2026-06-07T00:00:02', 'import', 'draft', 'b', 'ch')
        """
    )
    conn.execute("INSERT INTO plan_related_tickets(plan_name, ticket_id) VALUES ('p1', 't1')")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_repair_heals_dangling_plans_fk(tmp_path: Path) -> None:
    """Repair migration rebuilds children pointing at the dropped temp table."""
    conn = get_db(tmp_path / "murder.db")
    # Construct the exact corruption: children reference the temp table, which
    # is absent. SQLite does not validate FK targets at CREATE time, but FK
    # enforcement (enabled by get_db) would reject the seed INSERTs against the
    # missing parent table — disable it just while seeding the corrupt rows.
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE plans (
            name              TEXT PRIMARY KEY,
            status            TEXT NOT NULL CHECK (status IN ('draft','accepted','superseded')),
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            body              TEXT NOT NULL,
            frontmatter_json  TEXT NOT NULL DEFAULT '{}',
            body_hash         TEXT NOT NULL,
            file_hash         TEXT,
            materialized_path TEXT NOT NULL,
            revision_count    INTEGER NOT NULL DEFAULT 0,
            sync_state        TEXT NOT NULL DEFAULT 'synced'
                              CHECK (sync_state IN ('synced','parse_error')),
            parse_error       TEXT
        );
        CREATE TABLE plan_revisions (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_name        TEXT NOT NULL
                             REFERENCES "plans_old_single_master_migration"(name) ON DELETE CASCADE,
            created_at       TEXT NOT NULL,
            source           TEXT NOT NULL CHECK (source IN ('file','db','import')),
            status           TEXT NOT NULL,
            body             TEXT NOT NULL,
            frontmatter_json TEXT NOT NULL DEFAULT '{}',
            content_hash     TEXT NOT NULL
        );
        CREATE TABLE plan_related_tickets (
            plan_name TEXT NOT NULL
                      REFERENCES "plans_old_single_master_migration"(name) ON DELETE CASCADE,
            ticket_id TEXT NOT NULL,
            PRIMARY KEY (plan_name, ticket_id)
        );
        INSERT INTO plans
            (name, status, created_at, updated_at, body, body_hash,
             materialized_path, revision_count, sync_state)
        VALUES
            ('p1', 'draft', '2026-06-07T00:00:00', '2026-06-07T00:00:01',
             'body', 'h', 'plans/p1.md', 1, 'synced');
        INSERT INTO plan_revisions
            (plan_name, created_at, source, status, body, frontmatter_json, content_hash)
        VALUES ('p1', '2026-06-07T00:00:02', 'import', 'draft', 'b', '{}', 'ch');
        INSERT INTO plan_related_tickets(plan_name, ticket_id) VALUES ('p1', 't1');
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")

    init_db(conn)

    rev_sql = _plan_revisions_fk_target(conn)
    rel_sql = _plan_related_fk_target(conn)
    assert "plans_old_single_master_migration" not in rev_sql
    assert "plans_old_single_master_migration" not in rel_sql
    assert "REFERENCES plans(name)" in rev_sql
    assert "REFERENCES plans(name)" in rel_sql

    # Pre-existing rows preserved.
    assert conn.execute("SELECT plan_name, body FROM plan_revisions").fetchall()[0]["body"] == "b"
    assert (
        conn.execute("SELECT ticket_id FROM plan_related_tickets").fetchone()["ticket_id"] == "t1"
    )
    # Index recreated on plan_revisions.
    assert "idx_plan_revisions_plan" in _index_names(conn)

    # INSERT now succeeds and FK is satisfied.
    conn.execute(
        """
        INSERT INTO plan_revisions
            (plan_name, created_at, source, status, body, content_hash)
        VALUES ('p1', '2026-06-07T00:00:03', 'db', 'draft', 'b2', 'ch2')
        """
    )
    conn.execute("INSERT INTO plan_related_tickets(plan_name, ticket_id) VALUES ('p1', 't2')")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    # Repair must be a clean no-op on an already-healed DB.
    init_db(conn)
    assert "plans_old_single_master_migration" not in _plan_revisions_fk_target(conn)
    assert "REFERENCES plans(name)" in _plan_revisions_fk_target(conn)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_plans_migrations_idempotent(tmp_path: Path) -> None:
    """Running the full migration set twice is a no-op the second time."""
    conn = get_db(tmp_path / "murder.db")
    init_db(conn)
    rev_before = _plan_revisions_fk_target(conn)
    rel_before = _plan_related_fk_target(conn)

    # Second pass must not raise and must not change the schema.
    init_db(conn)
    assert _plan_revisions_fk_target(conn) == rev_before
    assert _plan_related_fk_target(conn) == rel_before
    assert "plans_old_single_master_migration" not in rev_before
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
