"""Direct application service for the scheduler's durable steering preference."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

_VALID_STEERING = frozenset({"auto", "pause", "prefer"})


def set_steering(
    db: sqlite3.Connection, *, harness: str, steering: str
) -> dict[str, object]:
    harness = harness.strip()
    if not harness:
        raise ValueError("scheduler.set_steering: harness required")
    if steering not in _VALID_STEERING:
        raise ValueError(f"scheduler.set_steering: unknown steering {steering!r}")
    db.execute(
        """
        INSERT INTO scheduler_steering (harness, steering, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(harness) DO UPDATE SET
            steering = excluded.steering,
            updated_at = excluded.updated_at
        """,
        (harness, steering, datetime.now(timezone.utc).isoformat()),
    )
    from murder.runtime.scheduler.projection import invalidate_schedule

    invalidate_schedule(db, subject_key=f"steering:{harness}")
    return {"handled": True, "harness": harness, "steering": steering}


__all__ = ["set_steering"]
