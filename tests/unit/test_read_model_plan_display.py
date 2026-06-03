from __future__ import annotations

from datetime import datetime

from murder.persistence import plans as plan_db
from murder.persistence.schema import get_db, init_db
from murder.plans.parser import render
from murder.plans.schema import Plan, PlanStatus
from murder.plans.sync import content_hash
from murder.service.read_model import ServiceReadModel
from murder.storage.paths import db_path, plan_md, report_md, reports_dir


def test_get_plan_display_reads_repo_relative_materialized_path(repo_root) -> None:
    conn = get_db(db_path(repo_root))
    init_db(conn)
    now = datetime(2026, 1, 1, 12, 0, 0)
    plan = Plan(
        name="display-plan",
        status=PlanStatus.DRAFT,
        created_at=now,
        updated_at=now,
        body="# Body\n\nvisible content\n",
    )
    path = plan_md(repo_root, plan.name)
    text = render(plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    plan_db.upsert_plan(
        conn,
        plan,
        content_hash=content_hash(text),
        materialized_path=str(path.relative_to(repo_root)),
        file_hash=content_hash(text),
        sync_state="synced",
        create_revision=True,
        revision_source="import",
    )

    display = ServiceReadModel(db_path(repo_root)).get_plan_display(plan.name)

    assert display is not None
    assert display.markdown == text
    assert "Missing materialized file" not in display.markdown


def test_get_reports_snapshot_lists_markdown_reports(repo_root) -> None:
    conn = get_db(db_path(repo_root))
    init_db(conn)
    conn.close()
    reports_dir(repo_root).mkdir(parents=True, exist_ok=True)
    report_md(repo_root, "first").write_text("# First\n\nbody\n", encoding="utf-8")
    (reports_dir(repo_root) / "ignore.txt").write_text("not a report", encoding="utf-8")

    snapshot = ServiceReadModel(db_path(repo_root)).get_reports_snapshot()

    assert [report.name for report in snapshot.reports] == ["first"]
    assert snapshot.reports[0].char_count > 0


def test_get_report_display_reads_report_markdown(repo_root) -> None:
    conn = get_db(db_path(repo_root))
    init_db(conn)
    conn.close()
    reports_dir(repo_root).mkdir(parents=True, exist_ok=True)
    text = "# Report\n\nvisible content\n"
    report_md(repo_root, "weekly").write_text(text, encoding="utf-8")

    display = ServiceReadModel(db_path(repo_root)).get_report_display("weekly")

    assert display is not None
    assert display.markdown == text
