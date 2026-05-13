from __future__ import annotations

import sqlite3
from datetime import datetime

from murder import db as dbmod
from murder.plans.schema import Plan


def test_plan_rows_revisions_and_related_links(memdb: sqlite3.Connection) -> None:
    plan = Plan(
        name="db-backed",
        created_at=datetime(2026, 5, 2, 12, 0, 0),
        related_tickets=["t900"],
        body="Body\n",
    )

    dbmod.upsert_plan(
        memdb,
        plan,
        content_hash="abc",
        materialized_path=".murder/plans/db-backed.md",
        file_hash="abc",
        last_materialized_hash="abc",
        revision_source="import",
    )

    row = dbmod.get_plan_row(memdb, "db-backed")
    assert row is not None
    assert row["revision_count"] == 1
    assert row["body"] == "Body\n"
    assert memdb.execute("SELECT COUNT(*) AS n FROM plan_revisions").fetchone()["n"] == 1
    assert (
        memdb.execute("SELECT ticket_id FROM plan_related_tickets").fetchone()["ticket_id"]
        == "t900"
    )
