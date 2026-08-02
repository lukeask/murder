"""Acceptance coverage for the consolidated per-user Turso database."""

from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest

from murder.facts.contracts import (
    FactActor,
    FactCorrelation,
    PrivateFactPayload,
    RetainedFactDraft,
)
from murder.facts.log import append_fact, replay_facts
from murder.state.persistence.backup import backup_database
from murder.state.persistence.connection import RepoDb, connect, open_repo_db, resolve_repository
from murder.state.persistence.legacy_merge import (
    merge_known_legacy_databases,
    merge_legacy_database,
)
from murder.state.persistence.repositories import forget_repository
from murder.state.storage.paths import db_path
from murder.state.storage.service_registry import ServiceSession
from tests.support.database import (
    SECOND_TEST_REPOSITORY_ID,
    TEST_REPOSITORY_ID,
    open_test_repo_db,
)


def _append(db_path: Path, repository_id: str, ordinal: int) -> int:
    db = open_test_repo_db(db_path, repository_id=repository_id, initialize=False)
    try:
        append_fact(
            db,
            RetainedFactDraft(
                fact_id=uuid4(),
                occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
                actor=FactActor(kind="service", id=f"writer-{ordinal}"),
                correlation=FactCorrelation(correlation_id=uuid4()),
                payload=PrivateFactPayload(kind="concurrency.seed", data={"n": ordinal}),
            ),
        )
        return len(replay_facts(db))
    finally:
        db.close()


def test_two_repository_connections_write_one_turso_file_without_busy(tmp_path: Path) -> None:
    path = tmp_path / "murder.db"
    initialized = open_test_repo_db(path)
    initialized.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                lambda args: _append(path, *args),
                ((TEST_REPOSITORY_ID, 1), (SECOND_TEST_REPOSITORY_ID, 2)),
            )
        )
    assert results == (1, 1)
    first = open_test_repo_db(path)
    second = open_test_repo_db(path, repository_id=SECOND_TEST_REPOSITORY_ID)
    try:
        assert len(replay_facts(first)) == 1
        assert len(replay_facts(second)) == 1
    finally:
        first.close()
        second.close()


def test_repository_id_survives_checkout_move(tmp_path: Path) -> None:
    db = open_test_repo_db(tmp_path / "murder.db")
    original = tmp_path / "original"
    original.mkdir()
    first = resolve_repository(db.conn, original)
    moved = tmp_path / "moved"
    original.rename(moved)
    assert resolve_repository(db.conn, moved) == first


def test_backup_is_a_consistent_readable_sqlite_copy(tmp_path: Path) -> None:
    source = tmp_path / "murder.db"
    db = open_test_repo_db(source)
    append_fact(
        db,
        RetainedFactDraft(
            fact_id=uuid4(),
            occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            actor=FactActor(kind="service", id="backup"),
            correlation=FactCorrelation(correlation_id=uuid4()),
            payload=PrivateFactPayload(kind="backup.seed", data={}),
        ),
    )
    db.close()
    destination = tmp_path / "backup.db"
    assert backup_database(source, destination) == destination
    with sqlite3.connect(destination) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT COUNT(*) FROM retained_facts").fetchone()[0] == 1


def test_legacy_merge_remaps_integer_rows_renames_sidecars_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    repo = tmp_path / "legacy-repo"
    legacy_dir = repo / ".murder"
    legacy_dir.mkdir(parents=True)
    legacy = legacy_dir / "murder.db"
    with sqlite3.connect(legacy) as source:
        source.execute(
            "CREATE TABLE retained_facts "
            "(sequence INTEGER PRIMARY KEY, fact_id TEXT UNIQUE, kind TEXT NOT NULL)"
        )
        source.execute("INSERT INTO retained_facts VALUES (1, 'legacy-fact', 'legacy.seed')")
    Path(str(legacy) + "-wal").touch()
    Path(str(legacy) + "-shm").touch()

    shared = open_test_repo_db(tmp_path / "shared.db")
    shared.conn.execute(
        """
        INSERT INTO retained_facts (
            fact_id, repository_id, kind, schema_version, occurred_at,
            recorded_at, actor_kind, actor_id, correlation_id, payload_json
        ) VALUES (?, ?, ?, 1, '2026-08-01T00:00:00+00:00',
                  '2026-08-01T00:00:00+00:00', 'service', 'seed', 'seed', '{}')
        """,
        ("existing", TEST_REPOSITORY_ID, "existing.seed"),
    )
    assert merge_legacy_database(shared.conn, repo)
    assert not merge_legacy_database(shared.conn, repo)
    migrated = legacy.with_name("murder.db.migrated")
    assert migrated.exists()
    assert Path(str(migrated) + "-wal").exists()
    assert Path(str(migrated) + "-shm").exists()
    rows = shared.conn.execute("SELECT sequence FROM retained_facts ORDER BY sequence").fetchall()
    assert [row[0] for row in rows] == [1, 2]


def _write_legacy_fact(repo: Path, fact_id: str) -> Path:
    legacy = repo / ".murder" / "murder.db"
    legacy.parent.mkdir(parents=True)
    with sqlite3.connect(legacy) as source:
        source.execute(
            "CREATE TABLE retained_facts "
            "(sequence INTEGER PRIMARY KEY, fact_id TEXT UNIQUE, kind TEXT NOT NULL)"
        )
        source.execute("INSERT INTO retained_facts VALUES (1, ?, 'legacy.seed')", (fact_id,))
    return legacy


def test_legacy_merge_leaves_other_live_service_database_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    current = tmp_path / "current"
    other = tmp_path / "other"
    current_legacy = _write_legacy_fact(current, "current-fact")
    other_legacy = _write_legacy_fact(other, "other-fact")
    session = ServiceSession(
        name="other-service",
        basename="other",
        path_hash="otherhash",
        repo_root=other,
        pid=os.getpid(),
        websocket_url="ws://127.0.0.1:1/api/ws",
    )
    monkeypatch.setattr(
        "murder.state.persistence.legacy_merge.list_service_sessions", lambda: [session]
    )

    shared = open_test_repo_db(tmp_path / "shared.db")
    try:
        assert merge_known_legacy_databases(shared.conn, current) == [current]
        assert current_legacy.with_name("murder.db.migrated").exists()
        assert other_legacy.exists()
        assert not other_legacy.with_name("murder.db.migrated").exists()
    finally:
        shared.close()


def test_concurrent_startup_merges_a_legacy_database_once(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    legacy = _write_legacy_fact(repo, "startup-fact")
    barrier = Barrier(2)

    def open_once() -> str:
        barrier.wait()
        db = open_repo_db(repo)
        try:
            return db.repository_id
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        repository_ids = tuple(pool.map(lambda _: open_once(), range(2)))

    assert repository_ids[0] == repository_ids[1]
    assert legacy.with_name("murder.db.migrated").exists()
    shared = connect(db_path())
    try:
        assert shared.execute("SELECT COUNT(*) FROM retained_facts").fetchone()[0] == 1
        assert shared.execute("SELECT COUNT(*) FROM repositories").fetchone()[0] == 1
    finally:
        shared.close()


def test_legacy_merge_skips_dangling_chunk_summary_block_pointer(tmp_path: Path) -> None:
    repo = tmp_path / "legacy-repo"
    legacy = repo / ".murder" / "murder.db"
    legacy.parent.mkdir(parents=True)
    with sqlite3.connect(legacy) as source:
        source.executescript(
            """
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE conversation_chunk_summaries (
                summary_id INTEGER PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                chunk_idx INTEGER NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE chunk_summary_blocks (
                summary_id INTEGER NOT NULL,
                block_id INTEGER NOT NULL,
                PRIMARY KEY (summary_id, block_id)
            );
            INSERT INTO conversations VALUES ('conv-1', 'agent-1', 'complete', 't', 't');
            INSERT INTO conversation_chunk_summaries VALUES (1, 'conv-1', 0, 'summary', 't');
            INSERT INTO chunk_summary_blocks VALUES (1, 99);
            """
        )

    shared = open_test_repo_db(tmp_path / "shared.db")
    try:
        assert merge_legacy_database(shared.conn, repo)
        assert (
            shared.conn.execute("SELECT COUNT(*) FROM conversation_chunk_summaries").fetchone()[0]
            == 1
        )
        assert shared.conn.execute("SELECT COUNT(*) FROM chunk_summary_blocks").fetchone()[0] == 0
    finally:
        shared.close()


def test_forget_removes_only_requested_partition(
    repo_db: RepoDb, second_repo_db: RepoDb
) -> None:
    append_fact(
        repo_db,
        RetainedFactDraft(
            fact_id=uuid4(), occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            actor=FactActor(kind="service", id="one"),
            correlation=FactCorrelation(correlation_id=uuid4()),
            payload=PrivateFactPayload(kind="forget.seed", data={}),
        ),
    )
    append_fact(
        second_repo_db,
        RetainedFactDraft(
            fact_id=uuid4(), occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            actor=FactActor(kind="service", id="two"),
            correlation=FactCorrelation(correlation_id=uuid4()),
            payload=PrivateFactPayload(kind="forget.seed", data={}),
        ),
    )
    repo_db.conn.execute(
        """
        INSERT INTO repositories(repository_id, root_path, created_at, last_seen_at)
        VALUES (?, '/one', '2026-08-01', '2026-08-01')
        """,
        (repo_db.repository_id,),
    )
    second_repo_db.conn.execute(
        """
        INSERT INTO repositories(repository_id, root_path, created_at, last_seen_at)
        VALUES (?, '/two', '2026-08-01', '2026-08-01')
        """,
        (second_repo_db.repository_id,),
    )
    assert forget_repository(repo_db.conn, repo_db.repository_id)
    assert replay_facts(repo_db) == ()
    assert len(replay_facts(second_repo_db)) == 1
