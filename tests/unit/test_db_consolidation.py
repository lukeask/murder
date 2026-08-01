"""Acceptance coverage for the consolidated per-user Turso database."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from murder.facts.contracts import (
    FactActor,
    FactCorrelation,
    PrivateFactPayload,
    RetainedFactDraft,
)
from murder.facts.log import append_fact, replay_facts
from murder.state.persistence.backup import backup_database
from murder.state.persistence.connection import resolve_repository
from murder.state.persistence.legacy_merge import merge_legacy_database
from murder.state.persistence.repositories import forget_repository
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
    tmp_path: Path, monkeypatch
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


def test_forget_removes_only_requested_partition(repo_db, second_repo_db) -> None:
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
