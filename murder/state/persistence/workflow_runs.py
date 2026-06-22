"""Persistence for launched workflow runs (the ``workflow_runs`` table).

A run is anchored by its parent "run" ticket; the row snapshots the
``WorkflowDef`` at launch and the stage.id -> ticket-id map so a later
coordination layer can interpret the run's graph without re-reading the
(possibly mutated) userspace definition. Run *state* is intentionally not
stored here — it is re-derived from the stage tickets' statuses.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkflowRunRecord:
    parent_ticket_id: str
    name: str
    definition_json: str
    stage_map: dict[str, str]  # stage.id -> ticket id
    created_at: str


def insert_workflow_run(
    conn: sqlite3.Connection,
    *,
    parent_ticket_id: str,
    name: str,
    definition_json: str,
    stage_map: dict[str, str],
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO workflow_runs(
            parent_ticket_id, name, definition_json, stage_map_json, created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (parent_ticket_id, name, definition_json, json.dumps(stage_map), created_at),
    )


def get_workflow_run(
    conn: sqlite3.Connection, parent_ticket_id: str
) -> WorkflowRunRecord | None:
    row = conn.execute(
        "SELECT * FROM workflow_runs WHERE parent_ticket_id = ?",
        (parent_ticket_id,),
    ).fetchone()
    return _record(row) if row is not None else None


def list_workflow_runs(conn: sqlite3.Connection) -> list[WorkflowRunRecord]:
    rows = conn.execute(
        "SELECT * FROM workflow_runs ORDER BY created_at, parent_ticket_id"
    ).fetchall()
    return [_record(row) for row in rows]


def _record(row: sqlite3.Row) -> WorkflowRunRecord:
    return WorkflowRunRecord(
        parent_ticket_id=str(row["parent_ticket_id"]),
        name=str(row["name"]),
        definition_json=str(row["definition_json"]),
        stage_map=json.loads(row["stage_map_json"]),
        created_at=str(row["created_at"]),
    )
