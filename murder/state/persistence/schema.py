"""SQLite schema, connection helpers, and table DDL.

All other persistence modules import ``get_db`` and ``init_db`` from here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from murder.permissions.persistence import ensure_permission_schema
from murder.runtime.sessions.persistence import ensure_session_schema

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

-- Verified harness decisions are durable control records, not generic bus
-- events.  They retain the exact identity binding required to resume an
-- already-recorded answer after a service crash.
CREATE TABLE IF NOT EXISTS structured_decisions (
    decision_request_id TEXT PRIMARY KEY,
    agent_id            TEXT NOT NULL,
    decision_kind       TEXT NOT NULL CHECK (decision_kind IN ('question', 'permission')),
    request_identity    TEXT NOT NULL,
    request_json        TEXT NOT NULL,
    response_json       TEXT,
    decided_by          TEXT,
    created_at          TEXT NOT NULL,
    responded_at        TEXT,
    CHECK ((response_json IS NULL) = (decided_by IS NULL)),
    CHECK ((response_json IS NULL) = (responded_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_structured_decisions_agent_kind_identity
    ON structured_decisions(agent_id, decision_kind, request_identity);
CREATE INDEX IF NOT EXISTS idx_structured_decisions_agent_response
    ON structured_decisions(agent_id, response_json);

-- Feature-owned retained fact log.  Unlike the legacy generalized events
-- table, rows here are immutable outcomes only: commands, addressed workflow
-- signals, terminal bytes, queries, and immediate decisions never enter this
-- table.  ``projection_inputs`` is the cursor-addressable transactional
-- invalidation boundary; it contains references, never authoritative state.
CREATE TABLE IF NOT EXISTS retained_facts (
    sequence            INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id             TEXT NOT NULL UNIQUE,
    kind                TEXT NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version >= 1),
    occurred_at         TEXT NOT NULL,
    recorded_at         TEXT NOT NULL,
    aggregate_kind      TEXT,
    aggregate_id        TEXT,
    aggregate_revision  INTEGER CHECK (aggregate_revision IS NULL OR aggregate_revision >= 0),
    actor_kind          TEXT NOT NULL,
    actor_id            TEXT NOT NULL,
    correlation_id      TEXT NOT NULL,
    causation_id        TEXT,
    trace_id            TEXT,
    payload_json        TEXT NOT NULL,
    CHECK ((aggregate_kind IS NULL) = (aggregate_id IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_retained_facts_kind_sequence
    ON retained_facts(kind, sequence);
CREATE INDEX IF NOT EXISTS idx_retained_facts_aggregate_sequence
    ON retained_facts(aggregate_kind, aggregate_id, sequence);

CREATE TRIGGER IF NOT EXISTS retained_facts_no_update
BEFORE UPDATE ON retained_facts
BEGIN
    SELECT RAISE(ABORT, 'retained facts are immutable');
END;

CREATE TABLE IF NOT EXISTS projection_inputs (
    sequence        INTEGER PRIMARY KEY AUTOINCREMENT,
    input_id        TEXT NOT NULL UNIQUE,
    source_fact_id  TEXT REFERENCES retained_facts(fact_id) ON DELETE RESTRICT,
    projection      TEXT NOT NULL,
    subject_key     TEXT NOT NULL,
    generation      INTEGER NOT NULL CHECK (generation >= 0),
    created_at      TEXT NOT NULL,
    UNIQUE (source_fact_id, projection, subject_key, generation)
);

CREATE INDEX IF NOT EXISTS idx_projection_inputs_projection_sequence
    ON projection_inputs(projection, sequence);

CREATE TRIGGER IF NOT EXISTS projection_inputs_no_update
BEFORE UPDATE ON projection_inputs
BEGIN
    SELECT RAISE(ABORT, 'projection inputs are immutable');
END;

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
-- Condensed summaries no longer live here: a single column cannot hold an
-- ordered sequence of chunk summaries nor their per-summary attribution
-- pointers.  They live in conversation_chunk_summaries + chunk_summary_blocks
-- (see below).  The old `condensed` column was dropped (migration
-- _migrate_conversation_chunk_summaries).
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

-- Condensed-view rolling chunk summaries (TUIchat Phase 4).
-- One row per summarized chunk of *intermediate* activity. The final reply is
-- never summarized (rendered verbatim), so it never appears here.
-- chunk_idx: 0-based order of the summary within the conversation.
-- summary: the condensed line for the chunk (already empty-summary guarded —
--   chunks that produced no usable summary are simply not written).
CREATE TABLE IF NOT EXISTS conversation_chunk_summaries (
    summary_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id)
                    ON DELETE CASCADE,
    chunk_idx       INTEGER NOT NULL,
    summary         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (conversation_id, chunk_idx)
);

CREATE INDEX IF NOT EXISTS idx_chunk_summaries_conv
    ON conversation_chunk_summaries(conversation_id, chunk_idx);

-- Attribution join: explicit pointers from a chunk summary to the N source
-- conversation_blocks it stands in for (block_id = conversation_blocks.id).
-- Explicit block-id pointers are the contract (NOT implicit ordinal ranges) so
-- the view can reveal/jump back to the exact blocks a summary replaces.
CREATE TABLE IF NOT EXISTS chunk_summary_blocks (
    summary_id  INTEGER NOT NULL REFERENCES conversation_chunk_summaries(summary_id)
                ON DELETE CASCADE,
    block_id    INTEGER NOT NULL,
    PRIMARY KEY (summary_id, block_id)
);

CREATE INDEX IF NOT EXISTS idx_chunk_summary_blocks_summary
    ON chunk_summary_blocks(summary_id);

CREATE TABLE IF NOT EXISTS harness_usage_snapshots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    harness        TEXT NOT NULL,
    source         TEXT NOT NULL,
    fetched_at     TEXT NOT NULL,
    status_json    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_harness_usage_snapshots_harness
    ON harness_usage_snapshots(harness, fetched_at);

-- Verified harness-control persistence.  This is deliberately separate from
-- conversation blocks and generic events/commands: frames/evidence are the
-- durable parser boundary, while operations/actions/effects are the durable
-- control boundary.  Do not use conversation text as a substitute for either.
CREATE TABLE IF NOT EXISTS harness_control_frames (
    frame_id          TEXT PRIMARY KEY,
    harness_id        TEXT NOT NULL,
    session_id        TEXT,
    captured_at       TEXT NOT NULL,
    width             INTEGER NOT NULL CHECK (width >= 0),
    height            INTEGER NOT NULL CHECK (height >= 0),
    raw_text          TEXT NOT NULL,
    ansi_preserved    INTEGER NOT NULL CHECK (ansi_preserved IN (0, 1)),
    pane_epoch        INTEGER NOT NULL CHECK (pane_epoch >= 0),
    capture_sequence  INTEGER NOT NULL CHECK (capture_sequence >= 0),
    stored_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_harness_control_frames_session
    ON harness_control_frames(harness_id, session_id, pane_epoch, capture_sequence);

CREATE TABLE IF NOT EXISTS harness_control_evidence (
    evidence_id         TEXT PRIMARY KEY,
    frame_id            TEXT NOT NULL REFERENCES harness_control_frames(frame_id)
                        ON DELETE CASCADE,
    harness_id          TEXT NOT NULL,
    parser_version      TEXT NOT NULL,
    evidence_type       TEXT NOT NULL,
    captured_at         TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    source_regions_json TEXT NOT NULL,
    diagnostics_json    TEXT NOT NULL,
    stored_at           TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_harness_control_evidence_frame
    ON harness_control_evidence(frame_id, evidence_type);
CREATE INDEX IF NOT EXISTS idx_harness_control_evidence_harness
    ON harness_control_evidence(harness_id, captured_at, evidence_type);

CREATE TABLE IF NOT EXISTS harness_control_observations (
    harness_id         TEXT NOT NULL,
    session_id         TEXT,
    pane_epoch         INTEGER NOT NULL CHECK (pane_epoch >= 0),
    capture_sequence   INTEGER NOT NULL CHECK (capture_sequence >= 0),
    semantic_sequence  INTEGER NOT NULL CHECK (semantic_sequence >= 0),
    captured_at        TEXT NOT NULL,
    snapshot_json      TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    stored_at          TEXT NOT NULL,
    PRIMARY KEY (harness_id, session_id, pane_epoch, capture_sequence, semantic_sequence)
);

CREATE INDEX IF NOT EXISTS idx_harness_control_observations_latest
    ON harness_control_observations(harness_id, session_id, pane_epoch DESC,
                                    capture_sequence DESC, semantic_sequence DESC);

CREATE TABLE IF NOT EXISTS harness_control_semantic_events (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    harness_id         TEXT NOT NULL,
    session_id         TEXT,
    pane_epoch         INTEGER NOT NULL CHECK (pane_epoch >= 0),
    capture_sequence   INTEGER NOT NULL CHECK (capture_sequence >= 0),
    semantic_sequence  INTEGER NOT NULL CHECK (semantic_sequence >= 0),
    event_type         TEXT NOT NULL,
    payload_json       TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    diagnostics_json   TEXT NOT NULL,
    captured_at        TEXT NOT NULL,
    stored_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_harness_control_semantic_events_harness
    ON harness_control_semantic_events(harness_id, session_id, id);

CREATE TABLE IF NOT EXISTS harness_control_operations (
    operation_id                    TEXT PRIMARY KEY,
    harness_id                      TEXT NOT NULL,
    session_id                      TEXT,
    capability                      TEXT NOT NULL,
    status                          TEXT NOT NULL,
    phase_type                      TEXT NOT NULL,
    phase_payload_json              TEXT NOT NULL,
    request_json                    TEXT NOT NULL,
    operation_state_json            TEXT NOT NULL,
    created_at                      TEXT NOT NULL,
    updated_at                      TEXT NOT NULL,
    deadline                        TEXT,
    attempt_count                   INTEGER NOT NULL DEFAULT 0,
    last_pane_epoch                 INTEGER,
    last_capture_sequence           INTEGER,
    last_semantic_sequence          INTEGER,
    warnings_json                   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_harness_control_operations_recovery
    ON harness_control_operations(harness_id, session_id, status, updated_at);

CREATE TABLE IF NOT EXISTS harness_control_actions (
    action_id                       TEXT PRIMARY KEY,
    operation_id                    TEXT NOT NULL REFERENCES harness_control_operations(operation_id)
                                    ON DELETE CASCADE,
    semantic_action_type            TEXT NOT NULL,
    semantic_action_json            TEXT NOT NULL,
    duplicate_policy                TEXT NOT NULL,
    selected_pane_epoch             INTEGER NOT NULL,
    selected_capture_sequence       INTEGER NOT NULL,
    selected_semantic_sequence      INTEGER NOT NULL,
    requested_at                    TEXT NOT NULL,
    expectation_json                TEXT NOT NULL,
    emitted_at                      TEXT,
    emission_error                  TEXT,
    emission_status                 TEXT NOT NULL CHECK (emission_status IN ('PENDING','EMITTED','FAILED'))
);

CREATE INDEX IF NOT EXISTS idx_harness_control_actions_operation
    ON harness_control_actions(operation_id, requested_at);

CREATE TABLE IF NOT EXISTS harness_control_effects (
    effect_id           TEXT PRIMARY KEY,
    action_id           TEXT NOT NULL REFERENCES harness_control_actions(action_id)
                        ON DELETE CASCADE,
    effect_type         TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    ordinal             INTEGER NOT NULL,
    emission_status     TEXT NOT NULL CHECK (emission_status IN ('PENDING','EMITTED','FAILED')),
    emitted_at          TEXT,
    emission_error      TEXT,
    UNIQUE(action_id, ordinal)
);

CREATE INDEX IF NOT EXISTS idx_harness_control_effects_action
    ON harness_control_effects(action_id, ordinal);

CREATE TABLE IF NOT EXISTS harness_control_decisions (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id                TEXT NOT NULL REFERENCES harness_control_operations(operation_id)
                                ON DELETE CASCADE,
    pane_epoch                  INTEGER NOT NULL,
    capture_sequence            INTEGER NOT NULL,
    semantic_sequence           INTEGER NOT NULL,
    phase_before                TEXT NOT NULL,
    predicate_results_json      TEXT NOT NULL,
    selected_decision           TEXT NOT NULL,
    selected_action_id          TEXT,
    reason                      TEXT NOT NULL,
    decided_at                  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_harness_control_decisions_operation
    ON harness_control_decisions(operation_id, id);

CREATE TABLE IF NOT EXISTS harness_usage_probe_sessions (
    harness    TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

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

-- Authoritative current workflow state.  parent_ticket_id, definition_json,
-- and stage_map_json retain the static ticket-DAG definition type as a
-- compatibility view; ticket statuses are not workflow run truth.
CREATE TABLE IF NOT EXISTS workflow_runs (
    workflow_id       TEXT PRIMARY KEY,
    definition_name   TEXT NOT NULL,
    definition_version INTEGER NOT NULL CHECK (definition_version >= 1),
    status            TEXT NOT NULL CHECK (status IN
                      ('running','waiting','completed','failed','cancelled','paused')),
    revision          INTEGER NOT NULL CHECK (revision >= 0),
    state_json        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    started_by_json   TEXT NOT NULL,
    correlation_json  TEXT NOT NULL,
    terminal_reason   TEXT,
    parent_ticket_id  TEXT UNIQUE REFERENCES tickets(id) ON DELETE SET NULL,
    definition_json   TEXT,
    stage_map_json    TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_workflow_runs_status
    ON workflow_runs(status, updated_at);

CREATE TABLE IF NOT EXISTS workflow_state_migrations (
    migration_id        TEXT PRIMARY KEY,
    workflow_id         TEXT NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE,
    migration_name      TEXT NOT NULL,
    from_schema_name    TEXT NOT NULL,
    from_schema_version INTEGER NOT NULL CHECK (from_schema_version >= 1),
    to_schema_name      TEXT NOT NULL,
    to_schema_version   INTEGER NOT NULL CHECK (to_schema_version >= 1),
    from_revision       INTEGER NOT NULL CHECK (from_revision >= 0),
    to_revision         INTEGER NOT NULL CHECK (to_revision >= 1),
    migrated_at         TEXT NOT NULL,
    UNIQUE (workflow_id, to_revision)
);

-- Addressed durable workflow inbox.  At-least-once producers converge on the
-- per-workflow deduplication key; consumption records the accepting revision.
CREATE TABLE IF NOT EXISTS workflow_signals (
    signal_id           TEXT PRIMARY KEY,
    workflow_id         TEXT NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE,
    deduplication_key   TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    consumed_at         TEXT,
    consumed_at_revision INTEGER,
    UNIQUE (workflow_id, deduplication_key),
    CHECK ((consumed_at IS NULL) = (consumed_at_revision IS NULL)),
    CHECK (consumed_at_revision IS NULL OR consumed_at_revision >= 1)
);

CREATE INDEX IF NOT EXISTS idx_workflow_signals_pending
    ON workflow_signals(workflow_id, consumed_at, created_at);

-- Explicit current wait set.  Satisfaction is recorded when an addressed
-- signal arrives and the complete set is replaced by transition application.
CREATE TABLE IF NOT EXISTS workflow_waits (
    wait_id                 TEXT PRIMARY KEY,
    workflow_id             TEXT NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE,
    created_at              TEXT NOT NULL,
    spec_json               TEXT NOT NULL,
    satisfied_at            TEXT,
    satisfied_by_signal_id  TEXT REFERENCES workflow_signals(signal_id) ON DELETE SET NULL,
    CHECK ((satisfied_at IS NULL) = (satisfied_by_signal_id IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_workflow_waits_current
    ON workflow_waits(workflow_id, satisfied_at, created_at);

CREATE TABLE IF NOT EXISTS activities (
    activity_id       TEXT PRIMARY KEY,
    workflow_id       TEXT NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE,
    workflow_revision INTEGER NOT NULL CHECK (workflow_revision >= 1),
    ordinal           INTEGER NOT NULL CHECK (ordinal >= 0),
    revision          INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    status            TEXT NOT NULL CHECK (status IN
                      ('pending','routing','waiting_admission','claimed','running',
                       'succeeded','failed','cancelled')),
    payload_json      TEXT NOT NULL,
    requirements_json TEXT NOT NULL,
    idempotency_key   TEXT NOT NULL UNIQUE,
    priority          INTEGER NOT NULL,
    retry_policy      TEXT NOT NULL,
    max_attempts      INTEGER NOT NULL CHECK (max_attempts >= 1),
    route_json        TEXT,
    route_id          TEXT,
    session_id        TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    claim_owner       TEXT,
    claim_fence       INTEGER NOT NULL DEFAULT 0 CHECK (claim_fence >= 0),
    claim_expires_at  TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (workflow_id, workflow_revision, ordinal)
);
CREATE INDEX IF NOT EXISTS idx_activities_queue
    ON activities(status, priority DESC, created_at);

CREATE TABLE IF NOT EXISTS activity_reservations (
    reservation_id        TEXT PRIMARY KEY,
    activity_id           TEXT NOT NULL UNIQUE REFERENCES activities(activity_id) ON DELETE CASCADE,
    reservation_keys_json TEXT NOT NULL,
    admitted_at           TEXT NOT NULL,
    expires_at             TEXT NOT NULL,
    released_at           TEXT
);

CREATE TABLE IF NOT EXISTS activity_reservation_locks (
    resource_key TEXT PRIMARY KEY,
    activity_id  TEXT NOT NULL REFERENCES activities(activity_id) ON DELETE CASCADE,
    expires_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_results (
    result_id     TEXT PRIMARY KEY,
    activity_id   TEXT NOT NULL REFERENCES activities(activity_id) ON DELETE CASCADE,
    attempt       INTEGER NOT NULL CHECK (attempt >= 0),
    outcome_json  TEXT NOT NULL,
    completed_at  TEXT NOT NULL,
    UNIQUE (activity_id, attempt)
);

CREATE TABLE IF NOT EXISTS workflow_triggers (
    trigger_id    TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    version       INTEGER NOT NULL CHECK (version >= 1),
    dedup_window_seconds INTEGER NOT NULL CHECK (dedup_window_seconds >= 0),
    spec_json     TEXT NOT NULL,
    target_json   TEXT NOT NULL,
    enabled       INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    created_at    TEXT NOT NULL,
    last_fired_at TEXT
);

CREATE TABLE IF NOT EXISTS trigger_firings (
    firing_id      TEXT PRIMARY KEY,
    trigger_id     TEXT NOT NULL REFERENCES workflow_triggers(trigger_id) ON DELETE CASCADE,
    occurrence_key TEXT NOT NULL,
    fired_at       TEXT NOT NULL,
    workflow_id    TEXT NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE,
    UNIQUE (trigger_id, occurrence_key)
);

CREATE TABLE IF NOT EXISTS trigger_cursors (
    trigger_id TEXT PRIMARY KEY REFERENCES workflow_triggers(trigger_id) ON DELETE CASCADE,
    cursor     TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trigger_manual_pending (
    trigger_id     TEXT NOT NULL REFERENCES workflow_triggers(trigger_id) ON DELETE CASCADE,
    occurrence_key TEXT NOT NULL,
    enqueued_at    TEXT NOT NULL,
    PRIMARY KEY (trigger_id, occurrence_key)
);

-- Side-effect requests and public facts returned by pure decisions are staged
-- transactionally.  Phase 4 consumers claim the typed payloads from here.
CREATE TABLE IF NOT EXISTS workflow_transition_outbox (
    outbox_id          TEXT PRIMARY KEY,
    workflow_id        TEXT NOT NULL REFERENCES workflow_runs(workflow_id) ON DELETE CASCADE,
    workflow_revision  INTEGER NOT NULL CHECK (workflow_revision >= 1),
    kind               TEXT NOT NULL CHECK (kind IN ('activity','approval','fact')),
    ordinal            INTEGER NOT NULL CHECK (ordinal >= 0),
    payload_json       TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    UNIQUE (workflow_id, workflow_revision, kind, ordinal)
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
        _migrate_conversation_chunk_summaries,
        _migrate_conversation_store,
        _migrate_conversation_queued_message,
        _migrate_drop_legacy_events,
        _migrate_drop_sentinel,
        _migrate_drop_ticket_write_set,
        _migrate_fact_log,
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

    # workflow_runs used to have a ticket id primary key and none of the
    # authoritative state columns.  Upgrade it before SCHEMA_SQL attempts to
    # create indexes and child tables that reference the new shape.
    _migrate_workflow_runs(conn)
    conn.executescript(SCHEMA_SQL)
    _migrate_drop_legacy_events(conn)
    _migrate_fact_log(conn)
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
    _migrate_conversation_chunk_summaries(conn)
    _migrate_map_summaries(conn)
    _migrate_scheduler_steering(conn)
    _migrate_history_status(conn)
    _migrate_runs_advanced_log_path(conn)
    _migrate_workflow_runs(conn)
    # The session/controller feature owns this DDL beside its DAO so fencing
    # rules cannot drift from a second schema copy. This idempotent call is the
    # central fresh-database and existing-database registration point.
    ensure_session_schema(conn)
    # Permission/approval tables are feature-owned; register them on every
    # init so fresh and upgraded databases share the same authoritative DDL.
    ensure_permission_schema(conn)
    ensure_notetaker_context_row(conn)


def db_path_for(repo_root: Path) -> Path:
    return repo_root / ".murder" / "murder.db"
