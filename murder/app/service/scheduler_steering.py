"""Direct application service for the scheduler's durable steering preference."""

from __future__ import annotations

from datetime import datetime, timezone

from murder.runtime.scheduler.projection import invalidate_schedule
from murder.state.persistence.connection import RepoDb

_VALID_STEERING = frozenset({"auto", "pause", "prefer"})


def set_steering(db: RepoDb, *, harness: str, steering: str) -> dict[str, object]:
    harness = harness.strip()
    if not harness:
        raise ValueError("scheduler.set_steering: harness required")
    if steering not in _VALID_STEERING:
        raise ValueError(f"scheduler.set_steering: unknown steering {steering!r}")
    db.conn.execute(
        """
        INSERT INTO scheduler_steering (repository_id, harness, steering, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(repository_id, harness) DO UPDATE SET
            steering = excluded.steering,
            updated_at = excluded.updated_at
        """,
        (db.repository_id, harness, steering, datetime.now(timezone.utc).isoformat()),
    )
    invalidate_schedule(db, subject_key=f"steering:{harness}")
    return {"handled": True, "harness": harness, "steering": steering}


__all__ = ["set_steering"]
