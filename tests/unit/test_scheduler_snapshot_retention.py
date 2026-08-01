from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from murder.runtime.scheduler.worker import SchedulerWorker
from murder.state.persistence.connection import RepoDb
from tests.support.database import open_test_repo_db

NOW = datetime(2026, 7, 24, 18, 0, tzinfo=timezone.utc)


@pytest.fixture()
def conn(tmp_path: Path) -> RepoDb:
    return open_test_repo_db(tmp_path / "test.db")


def _insert_frame(
    conn: RepoDb, *, frame_id: str, captured_at: datetime, sequence: int
) -> None:
    captured_at_iso = captured_at.isoformat()
    conn.conn.execute(
        """
        INSERT INTO harness_control_frames(
            repository_id, frame_id, harness_id, session_id, captured_at, width, height, raw_text,
            ansi_preserved, pane_epoch, capture_sequence, stored_at
        ) VALUES (?, ?, 'codex', 'session-a', ?, 80, 24, 'frame', 0, 1, ?, ?)
        """,
        (conn.repository_id, frame_id, captured_at_iso, sequence, captured_at_iso),
    )


def test_scheduler_prunes_harness_captures_after_five_days(conn: RepoDb) -> None:
    _insert_frame(
        conn,
        frame_id="older-than-five-days",
        captured_at=NOW - timedelta(days=5, microseconds=1),
        sequence=1,
    )
    _insert_frame(
        conn,
        frame_id="exactly-five-days",
        captured_at=NOW - timedelta(days=5),
        sequence=2,
    )

    SchedulerWorker()._prune_old_snapshots(conn, now=NOW)

    assert [
        row["frame_id"]
        for row in conn.conn.execute(
            "SELECT frame_id FROM harness_control_frames ORDER BY capture_sequence"
        )
    ] == ["exactly-five-days"]
