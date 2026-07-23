"""Scheduler-owned durable invalidations for the schedule projection."""

from __future__ import annotations

import sqlite3
from uuid import NAMESPACE_URL, uuid5

from murder.facts.contracts import ProjectionInputDraft
from murder.facts.log import append_projection_input


def invalidate_schedule(conn: sqlite3.Connection, *, subject_key: str) -> None:
    """Append the next schedule invalidation in the caller's transaction."""
    row = conn.execute(
        "SELECT COALESCE(MAX(generation), -1) + 1 AS generation "
        "FROM projection_inputs WHERE projection = 'schedule' AND subject_key = ?",
        (subject_key,),
    ).fetchone()
    generation = int(row["generation"])
    append_projection_input(
        conn,
        ProjectionInputDraft(
            input_id=uuid5(NAMESPACE_URL, f"schedule:{subject_key}:{generation}"),
            projection="schedule",
            subject_key=subject_key,
            generation=generation,
        ),
    )


__all__ = ["invalidate_schedule"]
