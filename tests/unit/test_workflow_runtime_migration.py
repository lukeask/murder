from __future__ import annotations

import json
import sqlite3
from uuid import UUID

from murder.state.persistence.migrations import _migrate_workflow_runs
from murder.state.persistence.workflow_runs import (
    get_workflow_run,
    list_workflow_waits,
)
from murder.work.workflows.runtime import (
    ExternalSignalWait,
    StageStatus,
    StaticDagWorkflowStateV1,
    WorkflowStatus,
)

LEGACY_DEFINITION_VERSION = 2


def _legacy_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE tickets (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        );
        INSERT INTO tickets(id, status, attempts, last_error)
        VALUES
            ('t001', 'planned', 0, NULL),
            ('t002', 'done', 1, NULL),
            ('t003', 'ready', 0, NULL);

        CREATE TABLE workflow_runs (
            parent_ticket_id TEXT PRIMARY KEY REFERENCES tickets(id) ON DELETE CASCADE,
            name             TEXT NOT NULL,
            definition_json  TEXT NOT NULL,
            stage_map_json   TEXT NOT NULL,
            created_at       TEXT NOT NULL
        );
        INSERT INTO workflow_runs(
            parent_ticket_id, name, definition_json, stage_map_json, created_at
        ) VALUES (
            't001',
            'legacy-dag',
            '{"name":"legacy-dag","definition_version":2,"stages":[]}',
            '{"build":"t002","test":"t003"}',
            '2026-07-18T10:00:00'
        );
        """
    )
    return conn


def test_legacy_workflow_backfill_creates_authoritative_state_and_waits() -> None:
    conn = _legacy_conn()
    _migrate_workflow_runs(conn)

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(workflow_runs)")}
    assert {"workflow_id", "state_json", "revision", "definition_version"} <= columns
    row = conn.execute("SELECT workflow_id FROM workflow_runs").fetchone()
    workflow_id = UUID(str(row["workflow_id"]))
    run = get_workflow_run(conn, workflow_id)
    assert run is not None
    assert run.parent_ticket_id == "t001"
    assert run.definition_name == "legacy-dag"
    assert run.definition_version == LEGACY_DEFINITION_VERSION
    assert run.status == WorkflowStatus.WAITING
    assert run.revision == 0
    assert run.created_at.utcoffset() is not None
    state = StaticDagWorkflowStateV1.model_validate(run.state.value)
    assert [(stage.stage_id, stage.status) for stage in state.stages] == [
        ("build", StageStatus.SUCCEEDED),
        ("test", StageStatus.READY),
    ]
    waits = list_workflow_waits(conn, workflow_id)
    assert len(waits) == 1
    assert waits[0].spec == ExternalSignalWait(
        signal_name="ticket.finished",
        correlation_key="t003",
    )


def test_workflow_migration_is_idempotent_with_stable_backfilled_uuid() -> None:
    conn = _legacy_conn()
    _migrate_workflow_runs(conn)
    first = conn.execute(
        "SELECT workflow_id, state_json, correlation_json FROM workflow_runs"
    ).fetchone()
    first_waits = conn.execute("SELECT wait_id, spec_json FROM workflow_waits").fetchall()

    _migrate_workflow_runs(conn)
    second = conn.execute(
        "SELECT workflow_id, state_json, correlation_json FROM workflow_runs"
    ).fetchone()
    second_waits = conn.execute("SELECT wait_id, spec_json FROM workflow_waits").fetchall()
    assert dict(first) == dict(second)
    assert [dict(row) for row in first_waits] == [dict(row) for row in second_waits]
    assert json.loads(str(second["state_json"]))["schema_name"] == "static_dag"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
