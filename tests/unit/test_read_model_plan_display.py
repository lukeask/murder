from __future__ import annotations

from datetime import datetime

from murder.app.service.read_model import ServiceReadModel
from murder.state.persistence import plans as plan_db
from murder.state.persistence.conversation import append_block, upsert_conversation
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.paths import db_path, plan_md, report_md, reports_dir
from murder.work.plans.parser import render
from murder.work.plans.schema import Plan, PlanStatus
from murder.work.plans.sync import content_hash


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


def test_get_conversations_snapshot_returns_active_histories(repo_root) -> None:
    conn = get_db(db_path(repo_root))
    init_db(conn)
    upsert_conversation(
        conn,
        conversation_id="crow-t001",
        agent_id="crow-t001",
        harness="codex",
        model="gpt-5.1",
        live_state="working",
        status="in_progress",
    )
    append_block(conn, "crow-t001", {"type": "user", "text": "hello"})
    upsert_conversation(
        conn,
        conversation_id="old",
        agent_id="old",
        harness="codex",
        status="stale",
    )
    append_block(conn, "old", {"type": "user", "text": "stale"})
    conn.close()

    snapshot = ServiceReadModel(db_path(repo_root)).get_conversations_snapshot()

    assert [c.conversation_id for c in snapshot.conversations] == ["crow-t001"]
    conversation = snapshot.conversations[0]
    assert conversation.agent_id == "crow-t001"
    assert conversation.harness == "codex"
    assert conversation.blocks[0].kind == "user"
    assert conversation.blocks[0].payload == {"type": "user", "text": "hello"}
