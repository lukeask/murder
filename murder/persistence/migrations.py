"""Schema migration functions — all ``_migrate_*`` helpers from murder/db.py."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from uuid import uuid4


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


_LEGACY_PLAN_MATERIALIZED_HASH_COLUMN = "last_" "materialized_" "hash"
_LEGACY_PLAN_CONFLICT_COLUMN = "conflict" "_reason"


def _migrate_ticket_last_error(conn: sqlite3.Connection) -> None:
    """Add last_error TEXT column to tickets for scheduler retry display."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)").fetchall()}
    if "last_error" not in cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN last_error TEXT")


def _migrate_ticket_archived_status(conn: sqlite3.Connection) -> None:
    """Add 'archived' to the tickets status CHECK constraint via table recreation."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tickets'"
    ).fetchone()
    if row is None or "'archived'" in str(row["sql"]):
        return
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        BEGIN;
        ALTER TABLE tickets RENAME TO tickets_old_archived_migration;
        CREATE TABLE tickets (
            id            TEXT PRIMARY KEY,
            title         TEXT NOT NULL,
            wave          INTEGER NOT NULL,
            status        TEXT NOT NULL CHECK (status IN
                          ('planned','ready','in_progress','blocked','done','failed','archived')),
            harness       TEXT,
            model         TEXT,
            schedule_at   TEXT,
            metadata_hash TEXT,
            metadata_file_hash TEXT,
            metadata_last_materialized_hash TEXT,
            metadata_materialized_path TEXT,
            metadata_sync_state TEXT NOT NULL DEFAULT 'synced',
            metadata_parse_error TEXT,
            metadata_conflict_reason TEXT,
            attempts      INTEGER NOT NULL DEFAULT 0,
            last_error    TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_wave   ON tickets(wave);
        CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
        CREATE INDEX IF NOT EXISTS idx_tickets_schedule_at ON tickets(schedule_at);
        CREATE INDEX IF NOT EXISTS idx_tickets_metadata_sync_state ON tickets(metadata_sync_state);
        INSERT INTO tickets SELECT
            id, title, wave, status, harness, model, schedule_at,
            metadata_hash, metadata_file_hash, metadata_last_materialized_hash,
            metadata_materialized_path, metadata_sync_state, metadata_parse_error,
            metadata_conflict_reason, attempts, last_error, created_at, updated_at
        FROM tickets_old_archived_migration;
        DROP TABLE tickets_old_archived_migration;
        COMMIT;
        PRAGMA foreign_keys = ON;
        """
    )


def _migrate_ticket_draft_status(conn: sqlite3.Connection) -> None:
    """Add 'draft' to the tickets status CHECK constraint via table recreation."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tickets'"
    ).fetchone()
    if row is None or "'draft'" in str(row["sql"]):
        return
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        BEGIN;
        ALTER TABLE tickets RENAME TO tickets_old_draft_migration;
        CREATE TABLE tickets (
            id            TEXT PRIMARY KEY,
            title         TEXT NOT NULL,
            wave          INTEGER NOT NULL,
            status        TEXT NOT NULL CHECK (status IN
                          ('draft','planned','ready','in_progress','blocked','done','failed','archived')),
            harness       TEXT,
            model         TEXT,
            schedule_at   TEXT,
            metadata_hash TEXT,
            metadata_file_hash TEXT,
            metadata_last_materialized_hash TEXT,
            metadata_materialized_path TEXT,
            metadata_sync_state TEXT NOT NULL DEFAULT 'synced',
            metadata_parse_error TEXT,
            metadata_conflict_reason TEXT,
            attempts      INTEGER NOT NULL DEFAULT 0,
            last_error    TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_wave   ON tickets(wave);
        CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
        CREATE INDEX IF NOT EXISTS idx_tickets_schedule_at ON tickets(schedule_at);
        CREATE INDEX IF NOT EXISTS idx_tickets_metadata_sync_state ON tickets(metadata_sync_state);
        INSERT INTO tickets SELECT
            id, title, wave, status, harness, model, schedule_at,
            metadata_hash, metadata_file_hash, metadata_last_materialized_hash,
            metadata_materialized_path, metadata_sync_state, metadata_parse_error,
            metadata_conflict_reason, attempts, last_error, created_at, updated_at
        FROM tickets_old_draft_migration;
        DROP TABLE tickets_old_draft_migration;
        COMMIT;
        PRAGMA foreign_keys = ON;
        """
    )


def _migrate_notes_identity_status(conn: sqlite3.Connection) -> None:
    """Add UUID-backed identity and retirement state to notes."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(notes)").fetchall()}
    if {"id", "status", "retired_at"} <= cols:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notes_status_updated ON notes(status, updated_at)"
        )
        return

    rows = conn.execute(
        "SELECT name, created_at, updated_at, body, materialized_path FROM notes"
    ).fetchall()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("BEGIN")
    try:
        conn.execute("ALTER TABLE notes RENAME TO notes_old_identity_migration")
        conn.execute(
            """
            CREATE TABLE notes (
                id                TEXT PRIMARY KEY,
                name              TEXT NOT NULL UNIQUE,
                created_at        TEXT NOT NULL,
                updated_at        TEXT NOT NULL,
                status            TEXT NOT NULL DEFAULT 'active'
                                  CHECK (status IN ('active','retired')),
                retired_at        TEXT,
                body              TEXT NOT NULL DEFAULT '',
                materialized_path TEXT NOT NULL
            )
            """
        )
        for row in rows:
            conn.execute(
                """
                INSERT INTO notes
                    (id, name, created_at, updated_at, status, retired_at, body, materialized_path)
                VALUES (?, ?, ?, ?, 'active', NULL, ?, ?)
                """,
                (
                    str(uuid4()),
                    row["name"],
                    row["created_at"],
                    row["updated_at"],
                    row["body"],
                    row["materialized_path"],
                ),
            )
        conn.execute("DROP TABLE notes_old_identity_migration")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_notes_status_updated ON notes(status, updated_at)"
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _migrate_agents_notetaker_role(conn: sqlite3.Connection) -> None:
    """Add 'notetaker' to the agents.role CHECK (planning notetaker agent)."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'agents'"
    ).fetchone()
    if row is None or "'notetaker'" in str(row["sql"]):
        return
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        BEGIN;
        ALTER TABLE agents RENAME TO agents_old_notetaker_migration;
        CREATE TABLE agents (
            agent_id          TEXT PRIMARY KEY,
            role              TEXT NOT NULL CHECK (role IN
                              ('collaborator','notetaker','sentinel','crow_handler','crow')),
            ticket_id         TEXT REFERENCES tickets(id) ON DELETE SET NULL,
            session           TEXT,
            status            TEXT NOT NULL CHECK (status IN
                              ('idle','running','blocked','escalating','done','failed','dead')),
            start_commit      TEXT,
            started_at        TEXT NOT NULL,
            last_heartbeat_at TEXT,
            pid               INTEGER
        );
        INSERT INTO agents
            (agent_id, role, ticket_id, session, status, start_commit,
             started_at, last_heartbeat_at, pid)
        SELECT agent_id, role, ticket_id, session, status, start_commit,
               started_at, last_heartbeat_at, pid
          FROM agents_old_notetaker_migration;
        DROP TABLE agents_old_notetaker_migration;
        COMMIT;
        PRAGMA foreign_keys = ON;
        """
    )


def _migrate_events_schema_version(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT 1 FROM pragma_table_info('events') WHERE name = 'schema_version'"
    ).fetchone()
    if row is not None:
        return
    conn.execute("ALTER TABLE events ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1")


def _migrate_agents_worktree_path(conn: sqlite3.Connection) -> None:
    """Track the execution worktree used by an agent session."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(agents)").fetchall()}
    if "worktree_path" not in cols:
        conn.execute("ALTER TABLE agents ADD COLUMN worktree_path TEXT")


def _migrate_agents_model(conn: sqlite3.Connection) -> None:
    """Persist the startup model requested for each agent session."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(agents)").fetchall()}
    if "model" not in cols:
        conn.execute("ALTER TABLE agents ADD COLUMN model TEXT")


def _migrate_agents_harness(conn: sqlite3.Connection) -> None:
    """Persist the harness kind on agents so rogue crows keep parser identity."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(agents)").fetchall()}
    if "harness" not in cols:
        conn.execute("ALTER TABLE agents ADD COLUMN harness TEXT")


def _migrate_ticket_metadata_columns(conn: sqlite3.Connection) -> None:
    """Add additive ticket metadata/scheduling columns for YAML sidecar sync."""
    ticket_cols = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)").fetchall()}
    migrations: tuple[tuple[str, str], ...] = (
        ("schedule_at", "TEXT"),
        ("metadata_hash", "TEXT"),
        ("metadata_file_hash", "TEXT"),
        ("metadata_last_materialized_hash", "TEXT"),
        ("metadata_materialized_path", "TEXT"),
        ("metadata_sync_state", "TEXT NOT NULL DEFAULT 'synced'"),
        ("metadata_parse_error", "TEXT"),
        ("metadata_conflict_reason", "TEXT"),
    )
    for name, ddl in migrations:
        if name in ticket_cols:
            continue
        conn.execute(f"ALTER TABLE tickets ADD COLUMN {name} {ddl}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_schedule_at ON tickets(schedule_at)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_tickets_metadata_sync_state ON tickets(metadata_sync_state)"
    )


def _migrate_role_names(conn: sqlite3.Connection) -> None:
    """Rename augur→crow_handler and monkey→crow in the agents table."""
    conn.execute("UPDATE agents SET role = 'crow_handler' WHERE role = 'augur'")
    conn.execute("UPDATE agents SET role = 'crow' WHERE role = 'monkey'")
    conn.execute(
        "UPDATE agents SET agent_id = REPLACE(agent_id, 'augur-', 'crow_handler-')"
        " WHERE agent_id LIKE 'augur-%'"
    )
    conn.execute(
        "UPDATE agents SET agent_id = REPLACE(agent_id, 'monkey-', 'crow-')"
        " WHERE agent_id LIKE 'monkey-%'"
    )


def _migrate_agents_failed_status(conn: sqlite3.Connection) -> None:
    """Allow the agent state machine to persist startup/runtime failures."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'agents'"
    ).fetchone()
    if row is None or "'failed'" in str(row["sql"]):
        return
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        BEGIN;
        ALTER TABLE agents RENAME TO agents_old_failed_migration;
        CREATE TABLE agents (
            agent_id          TEXT PRIMARY KEY,
            role              TEXT NOT NULL CHECK (role IN
                              ('collaborator','sentinel','crow_handler','crow')),
            ticket_id         TEXT REFERENCES tickets(id) ON DELETE SET NULL,
            session           TEXT,
            status            TEXT NOT NULL CHECK (status IN
                              ('idle','running','blocked','escalating','done','failed','dead')),
            start_commit      TEXT,
            started_at        TEXT NOT NULL,
            last_heartbeat_at TEXT,
            pid               INTEGER
        );
        INSERT INTO agents
            (agent_id, role, ticket_id, session, status, start_commit,
             started_at, last_heartbeat_at, pid)
        SELECT agent_id, role, ticket_id, session, status, start_commit,
               started_at, last_heartbeat_at, pid
          FROM agents_old_failed_migration;
        DROP TABLE agents_old_failed_migration;
        COMMIT;
        PRAGMA foreign_keys = ON;
        """
    )


def _migrate_completion_tables(conn: sqlite3.Connection) -> None:
    """Add check_results and completion_attempts tables for the completion coordinator."""
    existing = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "check_results" not in existing:
        conn.execute(
            """
            CREATE TABLE check_results (
                ticket_id   TEXT NOT NULL,
                check_name  TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                status      TEXT NOT NULL CHECK (status IN ('pass', 'fail')),
                data_json   TEXT,
                PRIMARY KEY (ticket_id, check_name, timestamp)
            )
            """
        )
    if "completion_attempts" not in existing:
        conn.execute(
            """
            CREATE TABLE completion_attempts (
                ticket_id   TEXT NOT NULL,
                check_name  TEXT NOT NULL,
                attempts    INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (ticket_id, check_name)
            )
            """
        )


def _migrate_drop_sentinel(conn: sqlite3.Connection) -> None:
    """Remove deceased sentinel role and its unused persistence table."""
    conn.execute("DROP TABLE IF EXISTS sentinel_state")

    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'agents'"
    ).fetchone()
    if row is None or "'sentinel'" not in str(row["sql"]):
        return

    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        BEGIN;
        DELETE FROM agents WHERE role = 'sentinel';
        ALTER TABLE agents RENAME TO agents_old_sentinel_migration;
        CREATE TABLE agents (
            agent_id          TEXT PRIMARY KEY,
            role              TEXT NOT NULL CHECK (role IN
                              ('collaborator','notetaker','crow_handler','crow','planner','planning_handler')),
            ticket_id         TEXT REFERENCES tickets(id) ON DELETE SET NULL,
            session           TEXT,
            status            TEXT NOT NULL CHECK (status IN
                              ('idle','running','blocked','escalating','done','failed','dead')),
            start_commit      TEXT,
            started_at        TEXT NOT NULL,
            last_heartbeat_at TEXT,
            pid               INTEGER
        );
        INSERT INTO agents
            (agent_id, role, ticket_id, session, status, start_commit,
             started_at, last_heartbeat_at, pid)
        SELECT agent_id, role, ticket_id, session, status, start_commit,
               started_at, last_heartbeat_at, pid
          FROM agents_old_sentinel_migration;
        DROP TABLE agents_old_sentinel_migration;
        COMMIT;
        PRAGMA foreign_keys = ON;
        """
    )


def _migrate_plans_single_master(conn: sqlite3.Connection) -> None:
    """Drop bidirectional-sync columns from plans; narrow sync_state CHECK.

    Idempotent: no-op once the new schema is in place.
    """
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='plans'").fetchone()
    if row is None:
        return
    cols = {column["name"] for column in conn.execute("PRAGMA table_info(plans)").fetchall()}
    if _LEGACY_PLAN_CONFLICT_COLUMN not in cols:
        return
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        BEGIN;
        ALTER TABLE plans RENAME TO plans_old_single_master_migration;
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
        INSERT INTO plans (name,status,created_at,updated_at,body,frontmatter_json,
                           body_hash,file_hash,materialized_path,revision_count,
                           sync_state,parse_error)
        SELECT name,status,created_at,updated_at,body,frontmatter_json,
               body_hash,file_hash,materialized_path,revision_count,
               CASE WHEN sync_state IN ('synced','parse_error') THEN sync_state
                    ELSE 'synced' END,
               parse_error
          FROM plans_old_single_master_migration;
        DROP TABLE plans_old_single_master_migration;
        CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status);
        COMMIT;
        PRAGMA foreign_keys = ON;
        """
    )
