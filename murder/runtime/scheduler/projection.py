"""Scheduler-owned durable invalidations for the schedule projection."""

from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from murder.facts.contracts import ProjectionInputDraft
from murder.facts.log import append_projection_input
from murder.state.persistence.connection import RepoDb


def invalidate_schedule(db: RepoDb, *, subject_key: str) -> None:
    """Append the next schedule invalidation in the caller's transaction."""
    row = db.conn.execute(
        "SELECT COALESCE(MAX(generation), -1) + 1 AS generation "
        "FROM projection_inputs WHERE repository_id = ? AND projection = 'schedule' AND subject_key = ?",
        (db.repository_id, subject_key),
    ).fetchone()
    generation = int(row["generation"])
    append_projection_input(
        db,
        ProjectionInputDraft(
            input_id=uuid5(NAMESPACE_URL, f"schedule:{subject_key}:{generation}"),
            projection="schedule",
            subject_key=subject_key,
            generation=generation,
        ),
    )


__all__ = ["invalidate_schedule"]
