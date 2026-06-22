"""SQLite schema, connection helpers, and table DDL.

All other persistence modules import ``get_db`` and ``init_db`` from here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from murder.state.storage.paths import MURDER_DIR_NAME

# fmt: off
SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    config_snapshot   TEXT NOT NULL,
    advanced_log_path TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN
                  ('draft','planned','ready','in_progress','blocked','done','failed','archived')),
    harness       TEXT,
    model         TEXT,
    worktree      TEXT,
    schedule_at   TEXT,
    parent_ticket_id TEXT,
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

CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);

CREATE TABLE IF NOT EXISTS ticket_deps (
    ticket_id      TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    depends_on_id  TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
    PRIMARY KEY (ticket_id, depends_on_id),
    CHECK (ticket_id != depends_on_id)
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
                      ('collaborator','notetaker','crow_handler','crow','planner','planning_handler')),
    ticket_id         TEXT REFERENCES tickets(id) ON DELETE SET NULL,
    session           TEXT,
    harness           TEXT,
    model             TEXT,
    worktree_path     TEXT,
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
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
    retired_at        TEXT,
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

CREATE TABLE IF NOT EXISTS reports (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
    retired_at        TEXT,
    body              TEXT NOT NULL DEFAULT '',
    materialized_path TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reports_updated ON reports(updated_at);

CREATE TABLE IF NOT EXISTS report_revisions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    report_name   TEXT NOT NULL REFERENCES reports(name) ON DELETE CASCADE,
    created_at    TEXT NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('agent','file_import','bootstrap')),
    body          TEXT NOT NULL,
    content_hash  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_report_revisions_report
    ON report_revisions(report_name, id);

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

-- Parsed conversation store (Phase 1.b).
-- One row per conversation session.  agent_id is a soft reference (no FK) so
-- tests can insert without a matching agents row, mirroring agent_messages.
-- harness_session_id: the resume-id captured on graceful /exit (1.g fills this).
-- live_state: the harness UI state at last parse (working/awaiting_input/awaiting_approval).
-- queued_message: a user message accepted while the harness was busy, held for
--   delivery at the next awaiting_input parse (cleared on delivery).
-- condensed: summary field from the transcript doc; stored for lossless round-trip.
-- status:
--   in_progress – conversation has an active tmux pane owned by murder.
--   complete    – harness exited gracefully; resume id was captured; history is final.
--   stale       – was in_progress at startup but its pane is gone (murder killed the
--                 session before /exit could run); treated as read-only history.
--                 Stale exists because a hard restart (ctrl-C, OOM, system reboot)
--                 leaves in_progress rows with no live pane — we need a third state
--                 distinct from "in_progress" (no pane) and "complete" (graceful).
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id    TEXT PRIMARY KEY,
    agent_id           TEXT NOT NULL,
    harness            TEXT,
    model              TEXT,
    harness_session_id TEXT,
    live_state         TEXT,
    condensed          TEXT,
    queued_message     TEXT,
    status             TEXT NOT NULL DEFAULT 'in_progress'
                       CHECK (status IN ('in_progress','complete','stale')),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conversations_agent ON conversations(agent_id);
CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(status);

-- Append-only block rows for each conversation.
-- kind mirrors the segment TypedDicts from segments.py plus:
--   assistant_intermediate  (assistant segment phase=intermediate)
--   assistant_final         (assistant segment phase=final)
--   notice                  (reserved for 1.f; usage/error notices emitted by the service)
-- payload_json: the full original segment dict stored verbatim for lossless round-trip.
-- sealed: 0 = live/mutable (the one trailing block that may still grow); 1 = immutable.
--   At most one sealed=0 row per conversation at any time.
-- ordinal: 0-based append order within the conversation.
CREATE TABLE IF NOT EXISTS conversation_blocks (
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
    mode       TEXT NOT NULL DEFAULT 'manual'
               CHECK (mode IN ('manual','autorun_ready','crow_magic')),
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

CREATE TABLE IF NOT EXISTS scheduler_steering (
    harness    TEXT PRIMARY KEY,
    steering   TEXT NOT NULL CHECK(steering IN ('auto','pause','prefer')),
    updated_at TEXT NOT NULL
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

-- Persisted model discovery results (one row per harness kind).
-- models_json: JSON array of {"id": ..., "label": ...} objects.
-- discovery_error: non-null when the last probe failed (null on success).
-- fetched_at: ISO8601 UTC timestamp of the last discovery attempt.
CREATE TABLE IF NOT EXISTS harness_models (
    harness         TEXT PRIMARY KEY,
    fetched_at      TEXT NOT NULL,
    models_json     TEXT NOT NULL,
    discovery_error TEXT
);

-- Codebase-map summaries, snapshotted per build keyed by commit SHA (t060).
-- One row per file/dir/root node: "what did the map look like at commit X".
-- path: repo-relative source path, or the dir path / 'ROOT' sentinel.
-- source_hash: sha256 of the source file (NULL for dir/root rollups).
CREATE TABLE IF NOT EXISTS map_summaries (
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

CREATE INDEX IF NOT EXISTS idx_map_summaries_commit ON map_summaries(commit_sha);

-- history_status: zero-LLM overlay over the durable user-message spine
-- (conversation_blocks kind='user'). The history view derives OPEN/STALE from
-- the block timestamp; a row here records an explicit terminal status. v0 only
-- ever writes 'dismissed'; the later LLM resolver writes richer statuses into
-- the same table without a schema change. Keyed by "<conversation_id>:<ordinal>".
CREATE TABLE IF NOT EXISTS history_status (
    item_id     TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    status_note TEXT,
    updated_at  TEXT NOT NULL
);

-- One row per launched workflow run. The parent "run" ticket (a pure tree
-- container, kept ``planned``) anchors the run; ON DELETE CASCADE means
-- deleting it drops the run record too.
--   definition_json: the WorkflowDef snapshot at launch time, so a later
--     coordination layer interprets the run's edges even if the userspace
--     definition is edited or deleted afterwards.
--   stage_map_json: JSON object mapping each stage.id -> its materialized
--     ticket id, so that layer resolves graph edges to concrete tickets.
-- Run *state* (which stages are done/blocked) is deliberately NOT stored: it
-- is re-derived from the stage tickets' statuses, so there is no cursor/edge
-- column to drift out of sync.
CREATE TABLE IF NOT EXISTS workflow_runs (
    parent_ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    definition_json  TEXT NOT NULL,
    stage_map_json   TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
"""
# fmt: on

NOTETAKER_CONTEXT_MATERIALIZED_REL = f"{MURDER_DIR_NAME}/notetakercontext.md"


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def get_db(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite connection in WAL mode with sane pragmas."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,
        check_same_thread=False,
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


def init_db(conn: sqlite3.Connection) -> None:
    """Apply SCHEMA_SQL idempotently."""
    from murder.state.persistence.migrations import (
        _migrate_agents_failed_status,
        _migrate_agents_harness,
        _migrate_agents_model,
        _migrate_agents_notetaker_role,
        _migrate_agents_worktree_path,
        _migrate_completion_tables,
        _migrate_conversation_store,
        _migrate_conversation_queued_message,
        _migrate_drop_sentinel,
        _migrate_drop_ticket_write_set,
        _migrate_events_schema_version,
        _migrate_history_status,
        _migrate_map_summaries,
        _migrate_notes_identity_status,
        _migrate_plans_single_master,
        _migrate_repair_plans_dangling_fk,
        _migrate_role_names,
        _migrate_runs_advanced_log_path,
        _migrate_scheduler_steering,
        _migrate_ticket_archived_status,
        _migrate_ticket_draft_status,
        _migrate_ticket_drop_skills,
        _migrate_ticket_drop_legacy_order,
        _migrate_ticket_last_error,
        _migrate_ticket_metadata_columns,
        _migrate_ticket_parent,
        _migrate_ticket_worktree,
        _migrate_workflow_runs,
    )
    from murder.state.persistence.notetaker import ensure_notetaker_context_row

    conn.executescript(SCHEMA_SQL)
    _migrate_events_schema_version(conn)
    _migrate_ticket_metadata_columns(conn)
    _migrate_ticket_last_error(conn)
    _migrate_agents_failed_status(conn)
    _migrate_agents_notetaker_role(conn)
    _migrate_role_names(conn)
    _migrate_ticket_archived_status(conn)
    _migrate_ticket_draft_status(conn)
    _migrate_ticket_worktree(conn)
    _migrate_ticket_drop_legacy_order(conn)
    _migrate_ticket_parent(conn)
    _migrate_ticket_drop_skills(conn)
    _migrate_notes_identity_status(conn)
    _migrate_completion_tables(conn)
    _migrate_drop_sentinel(conn)
    _migrate_plans_single_master(conn)
    _migrate_repair_plans_dangling_fk(conn)
    _migrate_agents_harness(conn)
    _migrate_agents_model(conn)
    _migrate_agents_worktree_path(conn)
    _migrate_drop_ticket_write_set(conn)
    _migrate_conversation_store(conn)
    _migrate_conversation_queued_message(conn)
    _migrate_map_summaries(conn)
    _migrate_scheduler_steering(conn)
    _migrate_history_status(conn)
    _migrate_runs_advanced_log_path(conn)
    _migrate_workflow_runs(conn)
    ensure_notetaker_context_row(conn)


def db_path_for(repo_root: Path) -> Path:
    return repo_root / ".murder" / "murder.db"
