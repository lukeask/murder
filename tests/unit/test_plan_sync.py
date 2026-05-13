from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from murder import db as dbmod
from murder.plans.schema import Plan
from murder.plans.sync import PlanSync


@pytest.mark.asyncio
async def test_sync_imports_orphan_plan_file(tmp_path, memdb: sqlite3.Connection) -> None:
    path = tmp_path / ".murder" / "plans" / "alpha.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
name: alpha
status: draft
created_at: '2026-05-02T12:00:00'
---
# Alpha
""",
        encoding="utf-8",
    )

    sync = PlanSync(tmp_path, memdb)
    await sync.reconcile_all()

    row = dbmod.get_plan_row(memdb, "alpha")
    assert row is not None
    assert row["sync_state"] == "synced"
    assert memdb.execute("SELECT COUNT(*) AS n FROM plan_revisions").fetchone()["n"] == 1


@pytest.mark.asyncio
async def test_poll_imports_new_plan_file_after_debounce(
    tmp_path, memdb: sqlite3.Connection
) -> None:
    path = tmp_path / ".murder" / "plans" / "delta.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
name: delta
status: draft
created_at: '2026-05-02T12:00:00'
---
# Delta
""",
        encoding="utf-8",
    )

    sync = PlanSync(tmp_path, memdb, debounce_s=0)
    await sync.poll_once()
    assert dbmod.get_plan_row(memdb, "delta") is None

    await sync.poll_once()
    row = dbmod.get_plan_row(memdb, "delta")
    assert row is not None
    assert row["sync_state"] == "synced"


@pytest.mark.asyncio
async def test_sync_materializes_missing_db_plan(tmp_path, memdb: sqlite3.Connection) -> None:
    plan = Plan(name="beta", created_at=datetime(2026, 5, 2, 12, 0, 0), body="Beta\n")
    dbmod.upsert_plan(
        memdb,
        plan,
        content_hash="dbhash",
        materialized_path=".murder/plans/beta.md",
        create_revision=False,
    )

    sync = PlanSync(tmp_path, memdb)
    await sync.reconcile_all()

    assert (tmp_path / ".murder" / "plans" / "beta.md").exists()


@pytest.mark.asyncio
async def test_sync_materializes_db_side_update(tmp_path, memdb: sqlite3.Connection) -> None:
    path = tmp_path / ".murder" / "plans" / "gamma.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
name: gamma
status: draft
created_at: '2026-05-02T12:00:00'
---
Old
""",
        encoding="utf-8",
    )
    sync = PlanSync(tmp_path, memdb)
    await sync.reconcile_all()

    plan = Plan(name="gamma", created_at=datetime(2026, 5, 2, 12, 0, 0), body="New\n")
    row = dbmod.get_plan_row(memdb, "gamma")
    assert row is not None
    dbmod.upsert_plan(
        memdb,
        plan,
        content_hash="changed-in-db",
        materialized_path=row["materialized_path"],
        file_hash=row["file_hash"],
        last_materialized_hash=row["last_materialized_hash"],
        revision_source="db",
    )

    await sync.reconcile_file(path)

    assert "New" in path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_sync_marks_parse_error_without_overwriting_db(
    tmp_path, memdb: sqlite3.Connection
) -> None:
    path = tmp_path / ".murder" / "plans" / "bad.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
name: bad
status: draft
created_at: '2026-05-02T12:00:00'
---
Good
""",
        encoding="utf-8",
    )
    sync = PlanSync(tmp_path, memdb)
    await sync.reconcile_all()

    path.write_text("---\n: bad\n---\nBroken\n", encoding="utf-8")
    await sync.reconcile_file(path)

    row = dbmod.get_plan_row(memdb, "bad")
    assert row is not None
    assert row["sync_state"] == "parse_error"


@pytest.mark.asyncio
async def test_poll_once_handles_file_deleted_between_scan_and_stat(
    tmp_path, memdb: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ".murder" / "plans" / "ephemeral.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        """---
name: ephemeral
status: draft
created_at: '2026-05-02T12:00:00'
---
# Ephemeral
""",
        encoding="utf-8",
    )

    sync = PlanSync(tmp_path, memdb, debounce_s=0)

    orig_scan = sync._scan_paths

    def _scan_then_delete() -> list:
        paths = orig_scan()
        if path.exists():
            path.unlink()
        return paths

    monkeypatch.setattr(sync, "_scan_paths", _scan_then_delete)
    await sync.poll_once()
