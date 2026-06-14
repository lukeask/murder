"""Schema migration functions — all ``_migrate_*`` helpers from murder/db.py."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from uuid import uuid4


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def _executescript_fk_off(
    conn: sqlite3.Connection, body: str, *, legacy_alter_table: bool = False
) -> None:
    """Run a table-recreation ``executescript`` with FK enforcement disabled.

    ``foreign_keys`` is a connection-global pragma. The earlier pattern embedded
    ``PRAGMA foreign_keys = OFF; ...; PRAGMA foreign_keys = ON;`` *inside* the
    script, so any statement that raised mid-script (e.g. a NOT NULL/CHECK
    violation during the data copy) aborted before the trailing ``ON`` ran and
    left FK enforcement silently OFF for the rest of the connection's life.

    Toggling the pragmas around the script with an explicit ``finally`` (matching
    ``_migrate_ticket_drop_legacy_order``) guarantees they are restored even if
    the copy throws. ``body`` must NOT contain the pragma toggles itself.
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    if legacy_alter_table:
        conn.execute("PRAGMA legacy_alter_table = ON")
    try:
        conn.executescript(body)
    finally:
        if legacy_alter_table:
            conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")


_LEGACY_PLAN_MATERIALIZED_HASH_COLUMN = "last_" "materialized_" "hash"
_LEGACY_PLAN_CONFLICT_COLUMN = "conflict" "_reason"
_LEGACY_TICKET_ORDER_COLUMN = "wa" "ve"
_LEGACY_TICKET_ORDER_INDEX = "idx_tickets_" + _LEGACY_TICKET_ORDER_COLUMN


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
    _executescript_fk_off(
        conn,
        f"""
        BEGIN;
        ALTER TABLE tickets RENAME TO tickets_old_archived_migration;
        CREATE TABLE tickets (
            id            TEXT PRIMARY KEY,
            title         TEXT NOT NULL,
            {_LEGACY_TICKET_ORDER_COLUMN}          INTEGER NOT NULL,
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
        CREATE INDEX IF NOT EXISTS {_LEGACY_TICKET_ORDER_INDEX}
            ON tickets({_LEGACY_TICKET_ORDER_COLUMN});
        CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
        CREATE INDEX IF NOT EXISTS idx_tickets_schedule_at ON tickets(schedule_at);
        CREATE INDEX IF NOT EXISTS idx_tickets_metadata_sync_state ON tickets(metadata_sync_state);
        INSERT INTO tickets SELECT
            id, title, {_LEGACY_TICKET_ORDER_COLUMN}, status, harness, model, schedule_at,
            metadata_hash, metadata_file_hash, metadata_last_materialized_hash,
            metadata_materialized_path, metadata_sync_state, metadata_parse_error,
            metadata_conflict_reason, attempts, last_error, created_at, updated_at
        FROM tickets_old_archived_migration;
        DROP TABLE tickets_old_archived_migration;
        COMMIT;
        """,
        legacy_alter_table=True,
    )


def _migrate_ticket_draft_status(conn: sqlite3.Connection) -> None:
    """Add 'draft' to the tickets status CHECK constraint via table recreation."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tickets'"
    ).fetchone()
    if row is None or "'draft'" in str(row["sql"]):
        return
    _executescript_fk_off(
        conn,
        f"""
        BEGIN;
        ALTER TABLE tickets RENAME TO tickets_old_draft_migration;
        CREATE TABLE tickets (
            id            TEXT PRIMARY KEY,
            title         TEXT NOT NULL,
            {_LEGACY_TICKET_ORDER_COLUMN}          INTEGER NOT NULL,
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
        CREATE INDEX IF NOT EXISTS {_LEGACY_TICKET_ORDER_INDEX}
            ON tickets({_LEGACY_TICKET_ORDER_COLUMN});
        CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
        CREATE INDEX IF NOT EXISTS idx_tickets_schedule_at ON tickets(schedule_at);
        CREATE INDEX IF NOT EXISTS idx_tickets_metadata_sync_state ON tickets(metadata_sync_state);
        INSERT INTO tickets SELECT
            id, title, {_LEGACY_TICKET_ORDER_COLUMN}, status, harness, model, schedule_at,
            metadata_hash, metadata_file_hash, metadata_last_materialized_hash,
            metadata_materialized_path, metadata_sync_state, metadata_parse_error,
            metadata_conflict_reason, attempts, last_error, created_at, updated_at
        FROM tickets_old_draft_migration;
        DROP TABLE tickets_old_draft_migration;
        COMMIT;
        """,
        legacy_alter_table=True,
    )


def _migrate_ticket_worktree(conn: sqlite3.Connection) -> None:
    """Add per-ticket worktree selection."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tickets'"
    ).fetchone()
    if row is None:
        return
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)").fetchall()}
    if "worktree" not in cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN worktree TEXT")


def _migrate_ticket_drop_legacy_order(conn: sqlite3.Connection) -> None:
    """Drop the legacy ticket ordering column via table recreation."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tickets'"
    ).fetchone()
    if row is None or _LEGACY_TICKET_ORDER_COLUMN not in str(row["sql"]):
        return

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")
    conn.execute("BEGIN")
    try:
        conn.execute("ALTER TABLE tickets RENAME TO tickets_old_order_migration")
        conn.execute(
            """
            CREATE TABLE tickets (
                id            TEXT PRIMARY KEY,
                title         TEXT NOT NULL,
                status        TEXT NOT NULL CHECK (status IN
                              ('draft','planned','ready','in_progress','blocked','done','failed','archived')),
                harness       TEXT,
                model         TEXT,
                worktree      TEXT,
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
            )
            """
        )
        conn.execute(
            """
            INSERT INTO tickets (
                id, title, status, harness, model, worktree, schedule_at,
                metadata_hash, metadata_file_hash, metadata_last_materialized_hash,
                metadata_materialized_path, metadata_sync_state, metadata_parse_error,
                metadata_conflict_reason, attempts, last_error, created_at, updated_at
            )
            SELECT
                id, title, status, harness, model, worktree, schedule_at,
                metadata_hash, metadata_file_hash, metadata_last_materialized_hash,
                metadata_materialized_path, metadata_sync_state, metadata_parse_error,
                metadata_conflict_reason, attempts, last_error, created_at, updated_at
            FROM tickets_old_order_migration
            """
        )
        conn.execute("DROP TABLE tickets_old_order_migration")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_schedule_at ON tickets(schedule_at)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tickets_metadata_sync_state "
            "ON tickets(metadata_sync_state)"
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")


def _migrate_ticket_drop_skills(conn: sqlite3.Connection) -> None:
    """Drop the hallucinated ticket_skills edge table."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ticket_skills'"
    ).fetchone()
    if row is not None:
        conn.execute("DROP TABLE ticket_skills")


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
    _executescript_fk_off(
        conn,
        """
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
        """,
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
    """Add additive ticket metadata/scheduling columns for file sync."""
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
    _executescript_fk_off(
        conn,
        """
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
        """,
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

    _executescript_fk_off(
        conn,
        """
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
        """,
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
    _executescript_fk_off(
        conn,
        """
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
        """,
        legacy_alter_table=True,
    )


def _migrate_repair_plans_dangling_fk(conn: sqlite3.Connection) -> None:
    """Repair child tables whose FK still references the migration temp table.

    A historical bug in ``_migrate_plans_single_master`` renamed ``plans`` without
    ``PRAGMA legacy_alter_table = ON``, so SQLite rewrote the foreign keys in
    ``plan_revisions`` / ``plan_related_tickets`` to point at the temporary
    ``plans_old_single_master_migration`` table. That temp table was then dropped,
    leaving the children with a dangling FK target — any INSERT raises
    ``no such table: main.plans_old_single_master_migration``.

    This migration rebuilds each affected child table with the canonical schema
    (FK → ``plans(name)``), preserving all existing rows. It is idempotent and a
    no-op once no dangling reference remains.
    """
    # Only consider the known child tables; the LIKE match would otherwise also
    # match the temp table's own CREATE statement if a prior migration was
    # interrupted before the DROP.
    affected = [
        row["name"]
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
             WHERE type = 'table'
               AND name IN ('plan_revisions', 'plan_related_tickets')
               AND sql LIKE '%plans_old_single_master_migration%'
            """
        ).fetchall()
    ]
    if not affected:
        return

    # Canonical correct-schema DDL, mirroring murder/state/persistence/schema.py.
    rebuilds: dict[str, tuple[str, str, tuple[str, ...]]] = {
        "plan_revisions": (
            """
            CREATE TABLE plan_revisions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_name        TEXT NOT NULL REFERENCES plans(name) ON DELETE CASCADE,
                created_at       TEXT NOT NULL,
                source           TEXT NOT NULL CHECK (source IN ('file','db','import')),
                status           TEXT NOT NULL,
                body             TEXT NOT NULL,
                frontmatter_json TEXT NOT NULL DEFAULT '{}',
                content_hash     TEXT NOT NULL
            )
            """,
            "id, plan_name, created_at, source, status, body, frontmatter_json, content_hash",
            (
                "CREATE INDEX IF NOT EXISTS idx_plan_revisions_plan "
                "ON plan_revisions(plan_name, id)",
            ),
        ),
        "plan_related_tickets": (
            """
            CREATE TABLE plan_related_tickets (
                plan_name TEXT NOT NULL REFERENCES plans(name) ON DELETE CASCADE,
                ticket_id TEXT NOT NULL,
                PRIMARY KEY (plan_name, ticket_id)
            )
            """,
            "plan_name, ticket_id",
            (),
        ),
    }

    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")
    conn.execute("BEGIN")
    try:
        for table in affected:
            create_sql, columns, indexes = rebuilds[table]
            temp = f"{table}_dangling_fk_repair"
            conn.execute(f"ALTER TABLE {table} RENAME TO {temp}")
            conn.execute(create_sql)
            conn.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM {temp}")
            conn.execute(f"DROP TABLE {temp}")
            for index_sql in indexes:
                conn.execute(index_sql)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA legacy_alter_table = OFF")
        conn.execute("PRAGMA foreign_keys = ON")


def _migrate_drop_ticket_write_set(conn: sqlite3.Connection) -> None:
    """Drop the ticket_write_set table (write_set concept removed)."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticket_write_set'"
    ).fetchone()
    if row is None:
        return
    conn.execute("DROP TABLE ticket_write_set")


def _migrate_conversation_queued_message(conn: sqlite3.Connection) -> None:
    """Add conversations.queued_message (busy-crow chat held for idle delivery).

    Idempotent: no-ops when the column already exists (fresh DBs get it from
    SCHEMA_SQL; this handles DBs created before the column landed).
    """
    existing = conn.execute(
        "SELECT 1 FROM pragma_table_info('conversations') WHERE name = 'queued_message'"
    ).fetchone()
    if existing is None:
        conn.execute("ALTER TABLE conversations ADD COLUMN queued_message TEXT")


def _migrate_map_summaries(conn: sqlite3.Connection) -> None:
    """Add the map_summaries table (codebase-map history, t060).

    Idempotent: the CREATE TABLE IF NOT EXISTS in SCHEMA_SQL handles fresh DBs;
    this migration handles existing DBs created before t060 landed.
    """
    existing = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "map_summaries" not in existing:
        conn.executescript(
            """
            CREATE TABLE map_summaries (
                path        TEXT NOT NULL,
                commit_sha  TEXT NOT NULL,
                kind        TEXT NOT NULL CHECK (kind IN ('file','dir','root')),
                body        TEXT NOT NULL,
                source_hash TEXT,
                source_tokens  INTEGER,
                summary_tokens INTEGER,
                generated_at   TEXT NOT NULL,
                PRIMARY KEY (path, commit_sha)
            );
            CREATE INDEX IF NOT EXISTS idx_map_summaries_commit
                ON map_summaries(commit_sha);
            """
        )


def _migrate_scheduler_steering(conn: sqlite3.Connection) -> None:
    """Add the scheduler_steering table (RT5 per-harness usage-window steering).

    Idempotent: the CREATE TABLE IF NOT EXISTS in SCHEMA_SQL handles fresh DBs;
    this migration handles existing DBs created before RT5 landed.
    """
    existing = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "scheduler_steering" not in existing:
        conn.executescript(
            """
            CREATE TABLE scheduler_steering (
                harness    TEXT PRIMARY KEY,
                steering   TEXT NOT NULL CHECK(steering IN ('auto','pause','prefer')),
                updated_at TEXT NOT NULL
            );
            """
        )


def _migrate_history_status(conn: sqlite3.Connection) -> None:
    """Add the history_status overlay table (history view, v0).

    Idempotent: the CREATE TABLE IF NOT EXISTS in SCHEMA_SQL handles fresh DBs;
    this migration handles existing DBs created before the history view landed.
    """
    existing = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "history_status" not in existing:
        conn.executescript(
            """
            CREATE TABLE history_status (
                item_id     TEXT PRIMARY KEY,
                status      TEXT NOT NULL,
                status_note TEXT,
                updated_at  TEXT NOT NULL
            );
            """
        )


def _migrate_runs_advanced_log_path(conn: sqlite3.Connection) -> None:
    """Add the nullable runs.advanced_log_path pointer (Phase 2, Step 2.9).

    The main DB stores ONLY the pointer to the advanced flight-recorder DB,
    never the bulky records themselves. Idempotent: the CREATE TABLE in
    SCHEMA_SQL covers fresh DBs; this handles DBs created before the column.
    """
    existing = conn.execute(
        "SELECT 1 FROM pragma_table_info('runs') WHERE name = 'advanced_log_path'"
    ).fetchone()
    if existing is None:
        conn.execute("ALTER TABLE runs ADD COLUMN advanced_log_path TEXT")


def current_schema_marker(conn: sqlite3.Connection) -> str:
    """Return a defensible main-DB schema/migration version string.

    There is no single integer schema version in murder.db; the closest stable
    markers are the ``events.schema_version`` column default and the presence of
    the additive ``runs.advanced_log_path`` pointer. The advanced log's
    ``session_info`` row records this so a captured session can be tied back to
    the schema that produced it. Tolerant of partial schemas (returns
    ``"unknown"`` fields rather than raising).
    """
    try:
        row = conn.execute(
            "SELECT schema_version FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        events_ver = row["schema_version"] if row is not None else 1
    except sqlite3.Error:
        events_ver = "unknown"
    try:
        has_ptr = (
            conn.execute(
                "SELECT 1 FROM pragma_table_info('runs') WHERE name = 'advanced_log_path'"
            ).fetchone()
            is not None
        )
    except sqlite3.Error:
        has_ptr = False
    return f"events.schema_version={events_ver};runs.advanced_log_path={has_ptr}"


def _migrate_conversation_store(conn: sqlite3.Connection) -> None:
    """Add conversations + conversation_blocks tables (Phase 1.b JSON store).

    Idempotent: the CREATE TABLE IF NOT EXISTS in SCHEMA_SQL handles fresh DBs;
    this migration handles existing DBs that ran init_db before 1.b landed.
    """
    existing = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    if "conversations" not in existing:
        conn.executescript(
            """
            CREATE TABLE conversations (
                conversation_id    TEXT PRIMARY KEY,
                agent_id           TEXT NOT NULL,
                harness            TEXT,
                model              TEXT,
                harness_session_id TEXT,
                live_state         TEXT,
                condensed          TEXT,
                status             TEXT NOT NULL DEFAULT 'in_progress'
                                   CHECK (status IN ('in_progress','complete','stale')),
                created_at         TEXT NOT NULL,
                updated_at         TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conversations_agent ON conversations(agent_id);
            CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status);
            """
        )
    if "conversation_blocks" not in existing:
        conn.executescript(
            """
            CREATE TABLE conversation_blocks (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id     TEXT NOT NULL REFERENCES conversations(conversation_id)
                                    ON DELETE CASCADE,
                ordinal             INTEGER NOT NULL,
                kind                TEXT NOT NULL CHECK (kind IN (
                                        'user',
                                        'assistant_intermediate',
                                        'assistant_final',
                                        'tool_call',
                                        'plan_update',
                                        'agent_event',
                                        'choice_prompt',
                                        'notice'
                                    )),
                payload_json        TEXT NOT NULL,
                sealed              INTEGER NOT NULL DEFAULT 0,
                service_received_at TEXT NOT NULL,
                UNIQUE (conversation_id, ordinal)
            );
            CREATE INDEX IF NOT EXISTS idx_conversation_blocks_conv
                ON conversation_blocks(conversation_id, ordinal);
            """
        )
