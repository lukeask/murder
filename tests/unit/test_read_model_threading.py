"""Thread-isolation coverage for service read-model snapshots."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from murder.app.service.read_model import ServiceReadModel
from murder.state.persistence import plans as plan_store
from murder.state.persistence.connection import Connection, RepoDb
from murder.work.plans.schema import Plan, PlanStatus
from murder.work.plans.sync import content_hash
from tests.support.database import open_test_repo_db

READ_COUNT = 16


class _ThreadBoundConnection:
    """Fail if the runtime-owned connection crosses its creating thread."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._owner_thread = threading.get_ident()
        self.row_factory = connection.row_factory

    @property
    def in_transaction(self) -> bool:
        return self._connection.in_transaction

    @property
    def isolation_level(self) -> str | None:
        return self._connection.isolation_level

    def execute(self, sql: str, parameters: Any = ()) -> Any:
        if threading.get_ident() != self._owner_thread:
            raise AssertionError("runtime database connection was used from a worker thread")
        return self._connection.execute(sql, parameters)

    def executemany(self, sql: str, parameters: Any) -> Any:
        return self._connection.executemany(sql, parameters)

    def executescript(self, sql_script: str) -> Any:
        return self._connection.executescript(sql_script)

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


def test_read_model_uses_isolated_connections_for_concurrent_threaded_reads(
    repo_root: Path,
) -> None:
    runtime_db = open_test_repo_db(repo_root / "murder.db")
    try:
        now = datetime(2026, 8, 1, 12, 0, 0)
        plan = Plan(
            name="thread-safe-read",
            status=PlanStatus("draft"),
            created_at=now,
            updated_at=now,
            body="# Thread-safe\n",
        )
        plan_store.upsert_plan(
            runtime_db,
            plan,
            content_hash=content_hash(plan.body),
            materialized_path=".murder/plans/thread-safe-read.md",
        )
        thread_bound_runtime_db = RepoDb(
            conn=_ThreadBoundConnection(runtime_db.conn),
            repository_id=runtime_db.repository_id,
        )
        read_model = ServiceReadModel(thread_bound_runtime_db, repo_root)

        with ThreadPoolExecutor(max_workers=READ_COUNT) as pool:
            snapshots = list(pool.map(lambda _: read_model.get_plans_snapshot(), range(READ_COUNT)))
    finally:
        runtime_db.close()

    assert len(snapshots) == READ_COUNT
    assert all(
        [plan.name for plan in snapshot.plans] == ["thread-safe-read"] for snapshot in snapshots
    )
