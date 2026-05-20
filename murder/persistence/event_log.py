"""Persistence for the events table."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


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
