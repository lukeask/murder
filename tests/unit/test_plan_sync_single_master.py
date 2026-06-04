from __future__ import annotations

import asyncio

from datetime import datetime

from murder.state.persistence import plans as plan_db
from murder.state.persistence.schema import get_db, init_db
from murder.work.plans.parser import parse, render
from murder.work.plans.schema import Plan, PlanStatus
from murder.work.plans.sync import PlanSync, content_hash
from murder.state.storage.paths import db_path, plan_md

_LEGACY_PLAN_MATERIALIZED_HASH_COLUMN = "last_" "materialized_" "hash"
_LEGACY_PLAN_CONFLICT_COLUMN = "conflict" "_reason"
_LEGACY_PLAN_CONFLICT_STATE = "con" "flict"


def _connect(repo_root):
    conn = get_db(db_path(repo_root))
    init_db(conn)
    return conn


def _plan(
    name: str,
    *,
    body: str = "# Body\n",
    updated_at: datetime | None = None,
) -> Plan:
    now = datetime(2026, 1, 1, 12, 0, 0)
    return Plan(
        name=name,
        status=PlanStatus.DRAFT,
        created_at=now,
        updated_at=updated_at or now,
        revisions=0,
        related_tickets=["t001"],
        frontmatter={"owner": "planner"},
        body=body,
    )


def test_reconcile_file_creates_revision_for_each_real_edit(repo_root) -> None:
    conn = _connect(repo_root)
    sync = PlanSync(repo_root, conn)
    path = plan_md(repo_root, "single-master")

    first = _plan("single-master", body="# First\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(first), encoding="utf-8")
    asyncio.run(sync.reconcile_file(path))

    second = _plan(
        "single-master",
        body="# Second\n",
        updated_at=datetime(2026, 1, 1, 12, 5, 0),
    )
    path.write_text(render(second), encoding="utf-8")
    asyncio.run(sync.reconcile_file(path))

    third = _plan(
        "single-master",
        body="# Third\n",
        updated_at=datetime(2026, 1, 1, 12, 10, 0),
    )
    path.write_text(render(third), encoding="utf-8")
    asyncio.run(sync.reconcile_file(path))

    row = plan_db.get_plan_row(conn, "single-master")
    revisions = conn.execute(
        "SELECT source, body FROM plan_revisions WHERE plan_name = ? ORDER BY id",
        ("single-master",),
    ).fetchall()

    assert row is not None
    assert row["sync_state"] == "synced"
    assert row["revision_count"] == 3
    assert row["body"] == "# Third\n"
    assert [(r["source"], r["body"]) for r in revisions] == [
        ("import", "# First\n"),
        ("file", "# Second\n"),
        ("file", "# Third\n"),
    ]


def test_reconcile_file_semantic_noop_updates_file_hash_without_new_revision(repo_root) -> None:
    conn = _connect(repo_root)
    sync = PlanSync(repo_root, conn)
    plan = _plan("semantic-noop")
    path = plan_md(repo_root, plan.name)
    canonical = render(plan)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical, encoding="utf-8")
    asyncio.run(sync.reconcile_file(path))
    initial_row = plan_db.get_plan_row(conn, plan.name)
    assert initial_row is not None

    variant = """---
updated_at: 2026-01-01T12:00:00
status: draft
name: semantic-noop
revisions: 0
related_tickets: [t001]
owner: planner
created_at: 2026-01-01T12:00:00
---
# Body
"""
    assert content_hash(variant) != content_hash(canonical)
    assert content_hash(render(parse(variant, default_name=path.stem))) == initial_row["body_hash"]

    path.write_text(variant, encoding="utf-8")
    asyncio.run(sync.reconcile_file(path))

    row = plan_db.get_plan_row(conn, plan.name)
    assert row is not None
    assert row["sync_state"] == "synced"
    assert row["revision_count"] == 1
    assert row["body_hash"] == initial_row["body_hash"]
    assert row["file_hash"] == content_hash(variant)


def test_reconcile_all_rebuilds_missing_plan_markdown_from_db(repo_root) -> None:
    conn = _connect(repo_root)
    plan = _plan("restore-me", body="# Restore\n")
    rendered = render(plan)
    path = plan_md(repo_root, plan.name)

    plan_db.upsert_plan(
        conn,
        plan,
        content_hash=content_hash(rendered),
        materialized_path=str(path.relative_to(repo_root)),
        file_hash=content_hash(rendered),
        sync_state="synced",
        create_revision=True,
        revision_source="db",
    )

    asyncio.run(PlanSync(repo_root, conn).reconcile_all())

    row = plan_db.get_plan_row(conn, plan.name)
    assert row is not None
    assert path.exists()
    assert parse(path.read_text(encoding="utf-8")).name == plan.name
    assert row["sync_state"] == "synced"
    assert row["revision_count"] == 1


def test_init_db_migrates_legacy_plans_table_to_single_master(repo_root) -> None:
    conn = get_db(db_path(repo_root))
    conn.execute(
        f"""
        CREATE TABLE plans (
            name              TEXT PRIMARY KEY,
            status            TEXT NOT NULL CHECK (status IN ('draft','accepted','superseded')),
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            body              TEXT NOT NULL,
            frontmatter_json  TEXT NOT NULL DEFAULT '{{}}',
            body_hash         TEXT NOT NULL,
            file_hash         TEXT,
            {_LEGACY_PLAN_MATERIALIZED_HASH_COLUMN} TEXT,
            materialized_path TEXT NOT NULL,
            revision_count    INTEGER NOT NULL DEFAULT 0,
            sync_state        TEXT NOT NULL DEFAULT 'synced' CHECK (sync_state IN
                              ('synced','missing_file','orphan_file','parse_error','{_LEGACY_PLAN_CONFLICT_STATE}')),
            {_LEGACY_PLAN_CONFLICT_COLUMN} TEXT,
            parse_error       TEXT
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO plans (
            name, status, created_at, updated_at, body, frontmatter_json,
            body_hash, file_hash, {_LEGACY_PLAN_MATERIALIZED_HASH_COLUMN}, materialized_path,
            revision_count, sync_state, {_LEGACY_PLAN_CONFLICT_COLUMN}, parse_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy",
            "draft",
            "2026-01-01T12:00:00",
            "2026-01-01T12:00:00",
            "# Legacy\n",
            "{}",
            "body-hash",
            "file-hash",
            "old-anchor",
            ".murder/plans/legacy.md",
            2,
            _LEGACY_PLAN_CONFLICT_STATE,
            "stale conflict",
            None,
        ),
    )

    init_db(conn)
    sql = str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'plans'"
        ).fetchone()["sql"]
    )
    row = plan_db.get_plan_row(conn, "legacy")
    columns = {
        column["name"] for column in conn.execute("PRAGMA table_info(plans)").fetchall()
    }

    assert row is not None
    assert row["sync_state"] == "synced"
    assert _LEGACY_PLAN_MATERIALIZED_HASH_COLUMN not in columns
    assert _LEGACY_PLAN_CONFLICT_COLUMN not in columns
    assert _LEGACY_PLAN_MATERIALIZED_HASH_COLUMN not in sql
    assert _LEGACY_PLAN_CONFLICT_COLUMN not in sql
    assert "parse_error" in sql
    assert f"'{_LEGACY_PLAN_CONFLICT_STATE}'" not in sql

    init_db(conn)
    sql_again = str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'plans'"
        ).fetchone()["sql"]
    )
    assert sql_again == sql
