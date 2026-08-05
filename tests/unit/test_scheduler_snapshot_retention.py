from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from murder.runtime.scheduler import worker as scheduler_worker
from murder.runtime.scheduler.worker import SchedulerWorker
from murder.state.persistence.connection import RepoDb, connect
from tests.support.database import (
    SECOND_TEST_REPOSITORY_ID,
    TEST_REPOSITORY_ID,
    open_test_repo_db,
)

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


def test_scheduler_prunes_raw_frames_after_ten_minutes(conn: RepoDb) -> None:
    _insert_frame(
        conn,
        frame_id="older-than-ten-minutes",
        captured_at=NOW - timedelta(minutes=10, microseconds=1),
        sequence=1,
    )
    _insert_frame(
        conn,
        frame_id="exactly-ten-minutes",
        captured_at=NOW - timedelta(minutes=10),
        sequence=2,
    )

    SchedulerWorker()._prune_old_snapshots(conn, now=NOW)

    assert [
        row["frame_id"]
        for row in conn.conn.execute(
            "SELECT frame_id FROM harness_control_frames ORDER BY capture_sequence"
        )
    ] == ["exactly-ten-minutes"]


def test_prune_in_thread_opens_fresh_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_file = tmp_path / "shared.db"
    host_db = open_test_repo_db(db_file)
    _insert_frame(
        host_db,
        frame_id="older-than-ten-minutes",
        captured_at=NOW - timedelta(minutes=10, microseconds=1),
        sequence=1,
    )
    _insert_frame(
        host_db,
        frame_id="exactly-ten-minutes",
        captured_at=NOW - timedelta(minutes=10),
        sequence=2,
    )

    opened: list[object] = []
    closed: list[object] = []

    def _connect_for_test() -> object:
        conn = connect(db_file)
        opened.append(conn)
        original_close = conn.close

        def _close() -> None:
            closed.append(conn)
            original_close()

        conn.close = _close  # type: ignore[method-assign]
        return conn

    monkeypatch.setattr(scheduler_worker, "connect", _connect_for_test)

    SchedulerWorker()._prune_old_snapshots_in_thread(TEST_REPOSITORY_ID, now=NOW)

    assert len(opened) == 1
    assert closed == opened
    assert opened[0] is not host_db.conn

    assert [
        row["frame_id"]
        for row in host_db.conn.execute(
            "SELECT frame_id FROM harness_control_frames ORDER BY capture_sequence"
        )
    ] == ["exactly-ten-minutes"]


@pytest.mark.asyncio
async def test_maybe_prune_passes_repository_id_not_live_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = SchedulerWorker()
    seen: list[str] = []

    def _fake_prune(repository_id: str, *, now: datetime | None = None) -> None:
        del now
        seen.append(repository_id)

    monkeypatch.setattr(worker, "_prune_old_snapshots_in_thread", _fake_prune)

    await worker._maybe_prune_old_snapshots(TEST_REPOSITORY_ID)

    assert seen == [TEST_REPOSITORY_ID]


@pytest.mark.asyncio
async def test_maybe_prune_logs_warning_on_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    worker = SchedulerWorker()

    def _boom(repository_id: str, *, now: datetime | None = None) -> None:
        del repository_id, now
        raise RuntimeError("boom")

    monkeypatch.setattr(worker, "_prune_old_snapshots_in_thread", _boom)

    with caplog.at_level("WARNING", logger=scheduler_worker.LOGGER.name):
        await worker._maybe_prune_old_snapshots(TEST_REPOSITORY_ID)

    assert any("harness capture prune failed" in record.message for record in caplog.records)
    assert worker._prune_in_progress is False


@pytest.mark.asyncio
async def test_multi_host_concurrent_prune_on_shared_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two repository schedulers can prune the shared DB without cross-talk or busy failures."""
    db_file = tmp_path / "shared.db"
    host_a = open_test_repo_db(db_file, repository_id=TEST_REPOSITORY_ID)
    host_b = open_test_repo_db(db_file, repository_id=SECOND_TEST_REPOSITORY_ID)
    try:
        for rid, prefix, db in (
            (TEST_REPOSITORY_ID, "a", host_a),
            (SECOND_TEST_REPOSITORY_ID, "b", host_b),
        ):
            del rid
            for i in range(8):
                _insert_frame(
                    db,
                    frame_id=f"{prefix}-old-{i}",
                    captured_at=NOW - timedelta(minutes=10, microseconds=1 + i),
                    sequence=i + 1,
                )
            _insert_frame(
                db,
                frame_id=f"{prefix}-keep",
                captured_at=NOW - timedelta(minutes=10),
                sequence=100,
            )

        opened: list[object] = []

        def _connect_for_test() -> object:
            conn = connect(db_file)
            opened.append(conn)
            return conn

        monkeypatch.setattr(scheduler_worker, "connect", _connect_for_test)

        worker_a = SchedulerWorker()
        worker_b = SchedulerWorker()

        # Overlap thread-pool prune work the same way multi-host ticks do.
        loop = asyncio.get_running_loop()
        with ThreadPoolExecutor(max_workers=2) as pool:
            await asyncio.gather(
                loop.run_in_executor(
                    pool,
                    lambda: worker_a._prune_old_snapshots_in_thread(
                        TEST_REPOSITORY_ID, now=NOW
                    ),
                ),
                loop.run_in_executor(
                    pool,
                    lambda: worker_b._prune_old_snapshots_in_thread(
                        SECOND_TEST_REPOSITORY_ID, now=NOW
                    ),
                ),
            )

        assert len(opened) == 2
        assert opened[0] is not opened[1]
        assert opened[0] is not host_a.conn
        assert opened[1] is not host_b.conn

        assert [
            row["frame_id"]
            for row in host_a.conn.execute(
                "SELECT frame_id FROM harness_control_frames "
                "WHERE repository_id = ? ORDER BY capture_sequence",
                (TEST_REPOSITORY_ID,),
            )
        ] == ["a-keep"]
        assert [
            row["frame_id"]
            for row in host_b.conn.execute(
                "SELECT frame_id FROM harness_control_frames "
                "WHERE repository_id = ? ORDER BY capture_sequence",
                (SECOND_TEST_REPOSITORY_ID,),
            )
        ] == ["b-keep"]
    finally:
        host_a.close()
        host_b.close()
