"""SQLite schema + access layer (D2).

`.murder/murder.db` is the source of truth for ticket metadata, status,
deps, write_sets, checklist items, agent state, events, escalations, and
runs. Markdown stays for prose only.

WAL mode is mandatory — CrowHandler reads concurrently with Sentinel writes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from murder.plans.schema import Plan
from murder.storage.paths import MURDER_DIR_NAME

if TYPE_CHECKING:
    from murder.tickets.schema import Ticket

# fmt: off
SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    config_snapshot   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    wave          INTEGER NOT NULL,
    status        TEXT NOT NULL CHECK (status IN
                  ('planned','ready','in_progress','blocked','done','failed')),
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

CREATE TABLE IF NOT EXISTS ticket_deps (
    ticket_id      TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    depends_on_id  TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    PRIMARY KEY (ticket_id, depends_on_id),
    CHECK (ticket_id != depends_on_id)
);

CREATE TABLE IF NOT EXISTS ticket_write_set (
    ticket_id  TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    path       TEXT NOT NULL,
    PRIMARY KEY (ticket_id, path)
);

CREATE TABLE IF NOT EXISTS ticket_skills (
    ticket_id  TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    skill      TEXT NOT NULL,
    PRIMARY KEY (ticket_id, skill)
);

CREATE TABLE IF NOT EXISTS checklist (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id  TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    ord        INTEGER NOT NULL,
    text       TEXT NOT NULL,
    done       INTEGER NOT NULL DEFAULT 0,
    done_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_checklist_ticket ON checklist(ticket_id);

CREATE TABLE IF NOT EXISTS agents (
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

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,
    run_id          TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    agent_id        TEXT,
    role            TEXT,
    ticket_id       TEXT,
    type            TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    payload_json    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_run    ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_ticket ON events(ticket_id);
CREATE INDEX IF NOT EXISTS idx_events_type   ON events(type);

CREATE TABLE IF NOT EXISTS commands (
    id               TEXT PRIMARY KEY,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    run_id           TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    agent_id         TEXT,
    role             TEXT,
    ticket_id        TEXT,
    target_worker    TEXT NOT NULL,
    kind             TEXT NOT NULL,
    payload_json     TEXT NOT NULL,
    correlation_id   TEXT NOT NULL,
    idempotency_key  TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN
                     ('pending','in_flight','done','failed','cancelled')),
    claimed_by       TEXT,
    lease_expires_at INTEGER,
    attempt_count    INTEGER NOT NULL DEFAULT 0,
    retryable        INTEGER NOT NULL DEFAULT 1,
    result_json      TEXT,
    last_error       TEXT
);

CREATE INDEX IF NOT EXISTS idx_commands_worker_status
    ON commands(target_worker, status, created_at);
CREATE INDEX IF NOT EXISTS idx_commands_lease
    ON commands(status, lease_expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_commands_idempotency
    ON commands(idempotency_key);

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id        TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    role             TEXT,
    ticket_id        TEXT,
    last_heartbeat_at TEXT NOT NULL,
    payload_json     TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_worker_heartbeats_run
    ON worker_heartbeats(run_id, last_heartbeat_at);

CREATE TABLE IF NOT EXISTS sentinel_state (
    key              TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    updated_at       TEXT NOT NULL,
    state_json       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS escalations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                TEXT NOT NULL,
    ticket_id         TEXT REFERENCES tickets(id) ON DELETE SET NULL,
    severity          INTEGER NOT NULL CHECK (severity BETWEEN 1 AND 3),
    reason            TEXT NOT NULL,
    to_recipient      TEXT NOT NULL CHECK (to_recipient IN ('user','collaborator')),
    resolved          INTEGER NOT NULL DEFAULT 0,
    resolved_at       TEXT,
    source_event_id   INTEGER REFERENCES events(id) ON DELETE SET NULL,
    body_path         TEXT
);

CREATE TABLE IF NOT EXISTS plans (
    name                   TEXT PRIMARY KEY,
    status                 TEXT NOT NULL CHECK (status IN
                           ('draft','accepted','superseded')),
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    body                   TEXT NOT NULL,
    frontmatter_json       TEXT NOT NULL DEFAULT '{}',
    body_hash              TEXT NOT NULL,
    file_hash              TEXT,
    last_materialized_hash TEXT,
    materialized_path      TEXT NOT NULL,
    revision_count         INTEGER NOT NULL DEFAULT 0,
    sync_state             TEXT NOT NULL DEFAULT 'synced' CHECK (sync_state IN
                           ('synced','missing_file','orphan_file','parse_error','conflict')),
    conflict_reason        TEXT,
    parse_error            TEXT
);

CREATE INDEX IF NOT EXISTS idx_plans_status ON plans(status);

CREATE TABLE IF NOT EXISTS plan_revisions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_name        TEXT NOT NULL REFERENCES plans(name) ON DELETE CASCADE,
    created_at       TEXT NOT NULL,
    source           TEXT NOT NULL CHECK (source IN ('file','db','import')),
    status           TEXT NOT NULL,
    body             TEXT NOT NULL,
    frontmatter_json TEXT NOT NULL DEFAULT '{}',
    content_hash     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_plan_revisions_plan ON plan_revisions(plan_name, id);

CREATE TABLE IF NOT EXISTS plan_related_tickets (
    plan_name TEXT NOT NULL REFERENCES plans(name) ON DELETE CASCADE,
    ticket_id TEXT NOT NULL,
    PRIMARY KEY (plan_name, ticket_id)
);

CREATE TABLE IF NOT EXISTS notes (
    name              TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    body              TEXT NOT NULL DEFAULT '',
    materialized_path TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_updated ON notes(updated_at);

CREATE TABLE IF NOT EXISTS note_revisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    note_name    TEXT NOT NULL REFERENCES notes(name) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    source       TEXT NOT NULL CHECK (source IN ('agent','file_import','bootstrap')),
    body         TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_note_revisions_note
    ON note_revisions(note_name, id);

CREATE TABLE IF NOT EXISTS notetaker_context (
    id                INTEGER PRIMARY KEY CHECK (id = 1),
    body              TEXT NOT NULL DEFAULT '',
    updated_at        TEXT NOT NULL,
    materialized_path TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notes_entries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    raw         TEXT NOT NULL,
    cleaned     TEXT NOT NULL,
    short_vers  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_entries_ts ON notes_entries(ts);

CREATE TABLE IF NOT EXISTS agent_messages (
    agent_id    TEXT NOT NULL,
    ordinal     INTEGER NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    body        TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    PRIMARY KEY (agent_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_agent ON agent_messages(agent_id);

CREATE TABLE IF NOT EXISTS harness_usage_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    harness        TEXT NOT NULL,
    source         TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    status_json    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_harness_usage_snapshots_harness
    ON harness_usage_snapshots(harness, fetched_at);

CREATE TABLE IF NOT EXISTS schedule_queue (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id             TEXT REFERENCES tickets(id) ON DELETE SET NULL,
    title                 TEXT NOT NULL,
    harness               TEXT,
    desired_start_at      TEXT,
    max_usage_percent     REAL,
    status                TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                          ('pending','scheduled','running','done','blocked','cancelled')),
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    notes                 TEXT
);

CREATE INDEX IF NOT EXISTS idx_schedule_queue_status
    ON schedule_queue(status, desired_start_at);

CREATE TABLE IF NOT EXISTS scheduler_state (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    mode       TEXT NOT NULL DEFAULT 'manual' CHECK (mode IN ('manual','autorun_ready','crow_magic')),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduler_params (
    harness              TEXT NOT NULL,
    window_key           TEXT NOT NULL,
    c_changeoff          REAL NOT NULL DEFAULT 0.7,
    t_alwaysyes          REAL NOT NULL DEFAULT 15.0,
    alwayscutoff         REAL NOT NULL DEFAULT 0.6,
    intensity            REAL NOT NULL DEFAULT 1.0,
    multiharness_cutoff  REAL,
    updated_at           TEXT NOT NULL,
    PRIMARY KEY (harness, window_key)
);

CREATE TABLE IF NOT EXISTS scheduler_decision_cache (
    harness              TEXT NOT NULL,
    window_key           TEXT NOT NULL,
    mode                 TEXT NOT NULL,
    decision             INTEGER NOT NULL,
    usage                REAL NOT NULL,
    t_until_reset        REAL NOT NULL,
    t_period             REAL NOT NULL,
    threshold            REAL NOT NULL,
    rationale            TEXT NOT NULL,
    kicked_ticket_id     TEXT,
    updated_at           TEXT NOT NULL,
    PRIMARY KEY (harness, window_key)
);
"""
# fmt: on


def connect(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection in WAL mode with sane pragmas."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None for explicit BEGIN/COMMIT control via executescript.
    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,
        check_same_thread=False,  # asyncio uses loop's default executor; serialize at app layer
        timeout=10.0,
    )
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA foreign_keys = ON;
        PRAGMA busy_timeout = 5000;
        """
    )
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply SCHEMA_SQL idempotently."""
    conn.executescript(SCHEMA_SQL)
    _migrate_events_schema_version(conn)
    _migrate_ticket_metadata_columns(conn)
    _migrate_ticket_last_error(conn)
    _migrate_agents_failed_status(conn)
    _migrate_agents_notetaker_role(conn)
    _migrate_role_names(conn)
    ensure_notetaker_context_row(conn)


def _migrate_ticket_last_error(conn: sqlite3.Connection) -> None:
    """Add last_error TEXT column to tickets for scheduler retry display."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(tickets)").fetchall()}
    if "last_error" not in cols:
        conn.execute("ALTER TABLE tickets ADD COLUMN last_error TEXT")


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


def _migrate_ticket_metadata_columns(conn: sqlite3.Connection) -> None:
    """Add additive ticket metadata/scheduling columns for YAML sidecar sync."""
    ticket_cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(tickets)").fetchall()
    }
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
        "CREATE INDEX IF NOT EXISTS idx_tickets_metadata_sync_state "
        "ON tickets(metadata_sync_state)"
    )


def _migrate_role_names(conn: sqlite3.Connection) -> None:
    """Rename augur→crow_handler and monkey→crow in the agents table."""
    conn.execute(
        "UPDATE agents SET role = 'crow_handler' WHERE role = 'augur'"
    )
    conn.execute(
        "UPDATE agents SET role = 'crow' WHERE role = 'monkey'"
    )
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


def db_path_for(repo_root: Path) -> Path:
    return repo_root / ".murder" / "murder.db"


# --- Iso timestamp helper ---------------------------------------------------

def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


NOTETAKER_CONTEXT_ROW_ID = 1
NOTETAKER_CONTEXT_MATERIALIZED_REL = f"{MURDER_DIR_NAME}/notetakercontext.md"


def ensure_notetaker_context_row(conn: sqlite3.Connection) -> None:
    """Ensure singleton row id=1 exists (survives repeated init_schema)."""
    conn.execute(
        """
        INSERT OR IGNORE INTO notetaker_context (id, body, updated_at, materialized_path)
        VALUES (?, '', ?, ?)
        """,
        (NOTETAKER_CONTEXT_ROW_ID, _now(), NOTETAKER_CONTEXT_MATERIALIZED_REL),
    )


# --- Plans ------------------------------------------------------------------

def insert_plan_revision(
    conn: sqlite3.Connection,
    plan: Plan,
    *,
    source: str,
    content_hash: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO plan_revisions
            (plan_name, created_at, source, status, body, frontmatter_json, content_hash)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan.name,
            _now(),
            source,
            plan.status.value,
            plan.body,
            json.dumps(plan.frontmatter, sort_keys=True, default=str),
            content_hash,
        ),
    )
    conn.execute(
        "UPDATE plans SET revision_count = revision_count + 1 WHERE name = ?",
        (plan.name,),
    )
    return int(cur.lastrowid or 0)


def upsert_plan(
    conn: sqlite3.Connection,
    plan: Plan,
    *,
    content_hash: str,
    materialized_path: str,
    file_hash: str | None = None,
    last_materialized_hash: str | None = None,
    sync_state: str = "synced",
    conflict_reason: str | None = None,
    parse_error: str | None = None,
    create_revision: bool = True,
    revision_source: str = "db",
) -> None:
    now = _now()
    existing = conn.execute(
        "SELECT revision_count FROM plans WHERE name = ?", (plan.name,)
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO plans
                (name, status, created_at, updated_at, body, frontmatter_json,
                 body_hash, file_hash, last_materialized_hash, materialized_path,
                 sync_state, conflict_reason, parse_error)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.name,
                plan.status.value,
                plan.created_at.isoformat(timespec="seconds"),
                plan.updated_at.isoformat(timespec="seconds") if plan.updated_at else now,
                plan.body,
                json.dumps(plan.frontmatter, sort_keys=True, default=str),
                content_hash,
                file_hash,
                last_materialized_hash,
                materialized_path,
                sync_state,
                conflict_reason,
                parse_error,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE plans
               SET status = ?, updated_at = ?, body = ?, frontmatter_json = ?,
                   body_hash = ?, file_hash = ?, last_materialized_hash = ?,
                   materialized_path = ?, sync_state = ?, conflict_reason = ?,
                   parse_error = ?
             WHERE name = ?
            """,
            (
                plan.status.value,
                plan.updated_at.isoformat(timespec="seconds") if plan.updated_at else now,
                plan.body,
                json.dumps(plan.frontmatter, sort_keys=True, default=str),
                content_hash,
                file_hash,
                last_materialized_hash,
                materialized_path,
                sync_state,
                conflict_reason,
                parse_error,
                plan.name,
            ),
        )
    conn.execute("DELETE FROM plan_related_tickets WHERE plan_name = ?", (plan.name,))
    for ticket_id in plan.related_tickets:
        conn.execute(
            """
            INSERT OR IGNORE INTO plan_related_tickets(plan_name, ticket_id)
            VALUES (?, ?)
            """,
            (plan.name, ticket_id),
        )
    if create_revision:
        insert_plan_revision(
            conn, plan, source=revision_source, content_hash=content_hash
        )


def list_plans(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.*,
               (SELECT COUNT(*) FROM plan_revisions r WHERE r.plan_name = p.name) AS revisions
          FROM plans p
         ORDER BY p.updated_at DESC, p.name
        """
    ).fetchall()
    return [dict(r) for r in rows]


def get_plan_row(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM plans WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def mark_plan_sync_state(
    conn: sqlite3.Connection,
    name: str,
    sync_state: str,
    *,
    file_hash: str | None = None,
    conflict_reason: str | None = None,
    parse_error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE plans
           SET sync_state = ?, file_hash = COALESCE(?, file_hash),
               conflict_reason = ?, parse_error = ?, updated_at = ?
         WHERE name = ?
        """,
        (sync_state, file_hash, conflict_reason, parse_error, _now(), name),
    )


# --- Notes (planning scratchpad docs) --------------------------------------

def get_note(conn: sqlite3.Connection, name: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM notes WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def list_notes(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT name, created_at, updated_at, materialized_path, length(body) AS size
          FROM notes
         ORDER BY name DESC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def latest_note_name(conn: sqlite3.Connection) -> str | None:
    row = conn.execute("SELECT name FROM notes ORDER BY name DESC LIMIT 1").fetchone()
    return str(row["name"]) if row else None


def upsert_note(
    conn: sqlite3.Connection, name: str, *, body: str, materialized_path: str
) -> None:
    now = _now()
    existing = conn.execute("SELECT 1 FROM notes WHERE name = ?", (name,)).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO notes (name, created_at, updated_at, body, materialized_path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (name, now, now, body, materialized_path),
        )
    else:
        conn.execute(
            "UPDATE notes SET updated_at = ?, body = ?, materialized_path = ? WHERE name = ?",
            (now, body, materialized_path, name),
        )


def insert_note_revision(
    conn: sqlite3.Connection,
    name: str,
    *,
    source: str,
    body: str,
    content_hash: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO note_revisions (note_name, created_at, source, body, content_hash)
        VALUES (?, ?, ?, ?, ?)
        """,
        (name, _now(), source, body, content_hash),
    )
    return int(cur.lastrowid or 0)


def list_note_revisions(conn: sqlite3.Connection, name: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, note_name, created_at, source, body, content_hash
          FROM note_revisions
         WHERE note_name = ?
         ORDER BY id
        """,
        (name,),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Notetaker context (singleton) + capture entries -------------------------

def get_notetaker_context(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM notetaker_context WHERE id = ?",
        (NOTETAKER_CONTEXT_ROW_ID,),
    ).fetchone()
    return dict(row) if row else None


def upsert_notetaker_context(
    conn: sqlite3.Connection, *, body: str, materialized_path: str
) -> None:
    conn.execute(
        """
        UPDATE notetaker_context
           SET body = ?, updated_at = ?, materialized_path = ?
         WHERE id = ?
        """,
        (body, _now(), materialized_path, NOTETAKER_CONTEXT_ROW_ID),
    )


def insert_notes_entry(
    conn: sqlite3.Connection, *, raw: str, cleaned: str, short_vers: str
) -> int:
    cur = conn.execute(
        """
        INSERT INTO notes_entries (ts, raw, cleaned, short_vers)
        VALUES (?, ?, ?, ?)
        """,
        (_now(), raw, cleaned, short_vers),
    )
    return int(cur.lastrowid or 0)


def list_recent_notes_entries(
    conn: sqlite3.Connection, *, limit: int = 50
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, ts, raw, cleaned, short_vers
          FROM notes_entries
         ORDER BY ts DESC, id DESC
         LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# --- Agent conversation log -------------------------------------------------
# Persisted, parsed transcript of an agent's interactive session — one row per
# turn. See murder/conversation.py for the merge/reconcile logic; this layer is
# just dumb storage.

def get_agent_messages(conn: sqlite3.Connection, agent_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT ordinal, role, body, captured_at FROM agent_messages "
        "WHERE agent_id = ? ORDER BY ordinal",
        (agent_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def replace_agent_messages(
    conn: sqlite3.Connection,
    agent_id: str,
    turns: list[tuple[str, str]],
    *,
    captured_at: str | None = None,
) -> None:
    """Atomically replace the whole stored transcript for ``agent_id``."""
    ts = captured_at or _now()
    conn.execute("DELETE FROM agent_messages WHERE agent_id = ?", (agent_id,))
    conn.executemany(
        "INSERT INTO agent_messages (agent_id, ordinal, role, body, captured_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [(agent_id, i, role, body, ts) for i, (role, body) in enumerate(turns)],
    )


# --- Runs -------------------------------------------------------------------

def insert_run(conn: sqlite3.Connection, run_id: str, config_snapshot: str) -> None:
    conn.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot) VALUES (?, ?, ?)",
        (run_id, _now(), config_snapshot),
    )


def end_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute("UPDATE runs SET ended_at = ? WHERE run_id = ?", (_now(), run_id))


# --- Tickets ----------------------------------------------------------------

def insert_ticket(conn: sqlite3.Connection, ticket: Ticket) -> None:
    """Insert ticket + its child rows in one transaction."""
    now = _now()
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            INSERT INTO tickets(id, title, wave, status, harness, model, attempts,
                                created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ticket.id,
                ticket.title,
                ticket.wave,
                ticket.status.value,
                ticket.harness,
                ticket.model,
                ticket.attempts,
                ticket.created_at.isoformat(timespec="seconds"),
                now,
            ),
        )
        for dep in ticket.deps:
            conn.execute(
                "INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES (?, ?)",
                (ticket.id, dep),
            )
        for path in ticket.write_set:
            conn.execute(
                "INSERT INTO ticket_write_set(ticket_id, path) VALUES (?, ?)",
                (ticket.id, str(path)),
            )
        for skill in ticket.skills:
            conn.execute(
                "INSERT INTO ticket_skills(ticket_id, skill) VALUES (?, ?)",
                (ticket.id, skill),
            )
        for item in ticket.checklist:
            conn.execute(
                "INSERT INTO checklist(ticket_id, ord, text, done) VALUES (?, ?, ?, ?)",
                (ticket.id, item.ord, item.text, 1 if item.done else 0),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def apply_ticket_carve_payload(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    title: str,
    harness: str | None,
    model: str | None,
    deps: list[str],
    skills: list[str],
    write_set: list[str],
    checklist: list[str],
) -> None:
    """Replace deps, write_set, skills, checklist and update ticket title/harness/model.

    Caller must wrap in a transaction if combined with status changes.
    """
    conn.execute(
        """
        UPDATE tickets
           SET title = ?, harness = ?, model = ?, updated_at = ?
         WHERE id = ?
        """,
        (title, harness, model, _now(), ticket_id),
    )
    conn.execute("DELETE FROM ticket_deps WHERE ticket_id = ?", (ticket_id,))
    for dep in deps:
        conn.execute(
            "INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES (?, ?)",
            (ticket_id, dep),
        )
    conn.execute("DELETE FROM ticket_write_set WHERE ticket_id = ?", (ticket_id,))
    for path in write_set:
        conn.execute(
            "INSERT INTO ticket_write_set(ticket_id, path) VALUES (?, ?)",
            (ticket_id, path),
        )
    conn.execute("DELETE FROM ticket_skills WHERE ticket_id = ?", (ticket_id,))
    for skill in skills:
        conn.execute(
            "INSERT INTO ticket_skills(ticket_id, skill) VALUES (?, ?)",
            (ticket_id, skill),
        )
    conn.execute("DELETE FROM checklist WHERE ticket_id = ?", (ticket_id,))
    for ord_, text in enumerate(checklist):
        conn.execute(
            "INSERT INTO checklist(ticket_id, ord, text, done) VALUES (?, ?, ?, 0)",
            (ticket_id, ord_, text),
        )


def get_ticket(conn: sqlite3.Connection, ticket_id: str) -> dict[str, Any] | None:
    """Return a dict of ticket + its child rows, or None."""
    row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
    if row is None:
        return None
    deps = [
        r["depends_on_id"]
        for r in conn.execute(
            "SELECT depends_on_id FROM ticket_deps WHERE ticket_id = ?", (ticket_id,)
        )
    ]
    write_set = [
        r["path"]
        for r in conn.execute(
            "SELECT path FROM ticket_write_set WHERE ticket_id = ?", (ticket_id,)
        )
    ]
    skills = [
        r["skill"]
        for r in conn.execute(
            "SELECT skill FROM ticket_skills WHERE ticket_id = ?", (ticket_id,)
        )
    ]
    checklist = [
        dict(r)
        for r in conn.execute(
            "SELECT id, ord, text, done, done_at FROM checklist "
            "WHERE ticket_id = ? ORDER BY ord",
            (ticket_id,),
        )
    ]
    return {
        **dict(row),
        "deps": deps,
        "write_set": write_set,
        "skills": skills,
        "checklist": checklist,
    }


def list_tickets_by_status(
    conn: sqlite3.Connection, status: str
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id FROM tickets WHERE status = ? ORDER BY wave, id", (status,)
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        t = get_ticket(conn, r["id"])
        if t is not None:
            out.append(t)
    return out


def list_tickets_in_wave(conn: sqlite3.Connection, wave: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id FROM tickets WHERE wave = ? ORDER BY id", (wave,)
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        t = get_ticket(conn, r["id"])
        if t is not None:
            out.append(t)
    return out


def update_ticket_status(
    conn: sqlite3.Connection, ticket_id: str, new_status: str
) -> None:
    conn.execute(
        "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, _now(), ticket_id),
    )


def get_ticket_status(conn: sqlite3.Connection, ticket_id: str) -> str | None:
    row = conn.execute(
        "SELECT status FROM tickets WHERE id = ?", (ticket_id,)
    ).fetchone()
    return row["status"] if row else None


def compute_ready(conn: sqlite3.Connection) -> list[str]:
    """Tickets whose deps are all `done` and that are currently `ready`.

    A ticket with no deps qualifies trivially. Result is sorted by wave then id
    so kickoff order is stable.
    """
    rows = conn.execute(
        """
        SELECT t.id
          FROM tickets AS t
          WHERE t.status = 'ready'
            AND NOT EXISTS (
                SELECT 1 FROM ticket_deps AS d
                  JOIN tickets AS dep ON dep.id = d.depends_on_id
                 WHERE d.ticket_id = t.id
                   AND dep.status != 'done'
            )
          ORDER BY t.wave, t.id
        """
    ).fetchall()
    return [r["id"] for r in rows]


def dependents_of(conn: sqlite3.Connection, ticket_id: str) -> list[str]:
    """Tickets that directly depend on `ticket_id`."""
    rows = conn.execute(
        "SELECT ticket_id FROM ticket_deps WHERE depends_on_id = ?", (ticket_id,)
    ).fetchall()
    return [r["ticket_id"] for r in rows]


# --- Checklist (D6) ---------------------------------------------------------

def set_checklist(conn: sqlite3.Connection, ticket_id: str, items: list[str]) -> None:
    """Replace a ticket's checklist. Used by Collaborator on carve."""
    conn.execute("BEGIN")
    try:
        conn.execute("DELETE FROM checklist WHERE ticket_id = ?", (ticket_id,))
        for ord_, text in enumerate(items):
            conn.execute(
                "INSERT INTO checklist(ticket_id, ord, text, done) VALUES (?, ?, ?, 0)",
                (ticket_id, ord_, text),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def check_off_item(conn: sqlite3.Connection, ticket_id: str, item_text: str) -> bool:
    """Mark first matching unchecked item as done; return True iff matched."""
    row = conn.execute(
        """
        SELECT id FROM checklist
         WHERE ticket_id = ? AND done = 0 AND text = ?
         ORDER BY ord LIMIT 1
        """,
        (ticket_id, item_text),
    ).fetchone()
    if row is None:
        return False
    conn.execute(
        "UPDATE checklist SET done = 1, done_at = ? WHERE id = ?",
        (_now(), row["id"]),
    )
    return True


def all_checked(conn: sqlite3.Connection, ticket_id: str) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM checklist WHERE ticket_id = ? AND done = 0",
        (ticket_id,),
    ).fetchone()
    return int(row["n"]) == 0


def checklist_progress(conn: sqlite3.Connection, ticket_id: str) -> tuple[int, int]:
    row = conn.execute(
        """
        SELECT
            SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END) AS done_n,
            COUNT(*) AS total
          FROM checklist WHERE ticket_id = ?
        """,
        (ticket_id,),
    ).fetchone()
    return int(row["done_n"] or 0), int(row["total"] or 0)


# --- Agents -----------------------------------------------------------------

def upsert_agent(
    conn: sqlite3.Connection,
    *,
    agent_id: str,
    role: str,
    ticket_id: str | None,
    session: str | None,
    status: str,
    start_commit: str | None = None,
    pid: int | None = None,
) -> None:
    """Insert or update an agent row."""
    now = _now()
    existing = conn.execute(
        "SELECT 1 FROM agents WHERE agent_id = ?", (agent_id,)
    ).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO agents
                (agent_id, role, ticket_id, session, status, start_commit,
                 started_at, last_heartbeat_at, pid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (agent_id, role, ticket_id, session, status, start_commit, now, now, pid),
        )
    else:
        conn.execute(
            """
            UPDATE agents
               SET role = ?, ticket_id = ?, session = ?, status = ?,
                   start_commit = COALESCE(?, start_commit),
                   last_heartbeat_at = ?,
                   pid = COALESCE(?, pid)
             WHERE agent_id = ?
            """,
            (role, ticket_id, session, status, start_commit, now, pid, agent_id),
        )


def heartbeat_agent(conn: sqlite3.Connection, agent_id: str) -> None:
    conn.execute(
        "UPDATE agents SET last_heartbeat_at = ? WHERE agent_id = ?",
        (_now(), agent_id),
    )


def set_agent_status(conn: sqlite3.Connection, agent_id: str, status: str) -> None:
    conn.execute(
        "UPDATE agents SET status = ?, last_heartbeat_at = ? WHERE agent_id = ?",
        (status, _now(), agent_id),
    )


# --- Events -----------------------------------------------------------------

def insert_event(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    agent_id: str,
    role: str,
    ticket_id: str | None,
    type: str,
    payload: dict[str, Any],
    schema_version: int = 1,
    ts: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO events(
            ts, run_id, agent_id, role, ticket_id, type, schema_version, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts or _now(),
            run_id,
            agent_id,
            role,
            ticket_id,
            type,
            schema_version,
            json.dumps(payload, default=str),
        ),
    )
    return int(cur.lastrowid or 0)


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
) -> dict[str, Any] | None:
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
    return dict(claimed) if claimed else None


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
        next_attempt = int(row["attempt_count"] or 0) + 1
        if int(row["retryable"] or 0) == 1 and next_attempt < max_attempts:
            conn.execute(
                """
                UPDATE commands
                   SET status = 'pending',
                       claimed_by = NULL,
                       lease_expires_at = NULL,
                       attempt_count = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (next_attempt, now, command_id),
            )
            retried.append(command_id)
            continue
        conn.execute(
            """
            UPDATE commands
               SET status = 'failed',
                   claimed_by = NULL,
                   lease_expires_at = NULL,
                   attempt_count = ?,
                   last_error = COALESCE(last_error, 'command lease expired'),
                   updated_at = ?
             WHERE id = ?
            """,
            (next_attempt, now, command_id),
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


def get_worker_heartbeat(conn: sqlite3.Connection, worker_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM worker_heartbeats WHERE worker_id = ?",
        (worker_id,),
    ).fetchone()
    return dict(row) if row else None


def upsert_sentinel_state(
    conn: sqlite3.Connection,
    *,
    key: str,
    run_id: str,
    state: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO sentinel_state(key, run_id, updated_at, state_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            run_id = excluded.run_id,
            updated_at = excluded.updated_at,
            state_json = excluded.state_json
        """,
        (key, run_id, _now(), json.dumps(state, default=str)),
    )


def get_sentinel_state(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT state_json FROM sentinel_state WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return None
    raw = row["state_json"]
    if not raw:
        return {}
    loaded = json.loads(raw)
    return loaded if isinstance(loaded, dict) else {}


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


# --- Escalations ------------------------------------------------------------

def insert_escalation(
    conn: sqlite3.Connection,
    *,
    ticket_id: str | None,
    severity: int,
    reason: str,
    to_recipient: str,
    source_event_id: int | None = None,
    body_path: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO escalations
            (ts, ticket_id, severity, reason, to_recipient, source_event_id, body_path)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (_now(), ticket_id, severity, reason, to_recipient, source_event_id, body_path),
    )
    return int(cur.lastrowid or 0)


def list_pending_escalations(
    conn: sqlite3.Connection, recipient: str | None = None
) -> list[dict[str, Any]]:
    if recipient is None:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE resolved = 0 ORDER BY ts DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE resolved = 0 AND to_recipient = ? "
            "ORDER BY ts DESC",
            (recipient,),
        ).fetchall()
    return [dict(r) for r in rows]


def resolve_escalation(conn: sqlite3.Connection, escalation_id: int) -> None:
    conn.execute(
        "UPDATE escalations SET resolved = 1, resolved_at = ? WHERE id = ?",
        (_now(), escalation_id),
    )
