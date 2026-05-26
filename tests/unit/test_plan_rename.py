from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from murder.agents.base import AgentRole, AgentStatus
from murder.orchestration.orchestrator import Orchestrator
from murder.persistence import plans as plan_db
from murder.persistence.agents import upsert_agent
from murder.persistence.schema import get_db, init_db
from murder.plans.parser import parse, render, write
from murder.plans.schema import Plan, PlanStatus
from murder.plans.sync import PlanSync, content_hash
from murder.service.agent_registry import AgentRegistry
from murder.storage.paths import db_path, deprecated_plans_dir, plan_md


def _connect(repo_root):
    conn = get_db(db_path(repo_root))
    init_db(conn)
    return conn


def _plan(name: str, body: str = "# Body\n") -> Plan:
    now = datetime(2026, 1, 1, 12, 0, 0)
    return Plan(
        name=name,
        status=PlanStatus.DRAFT,
        created_at=now,
        updated_at=now,
        revisions=0,
        related_tickets=["t001"],
        frontmatter={"owner": "planner"},
        body=body,
    )


def _insert_plan(conn, repo_root, plan: Plan) -> None:
    text = render(plan)
    plan_db.upsert_plan(
        conn,
        plan,
        content_hash=content_hash(text),
        materialized_path=str(plan_md(repo_root, plan.name).relative_to(repo_root)),
        file_hash=content_hash(text),
        last_materialized_hash=content_hash(text),
        sync_state="synced",
        create_revision=True,
        revision_source="db",
    )

def test_rename_plan_moves_primary_key_and_child_references(repo_root) -> None:
    conn = _connect(repo_root)
    _insert_plan(conn, repo_root, _plan("old"))
    conn.execute(
        "CREATE TABLE plan_tickets(plan_name TEXT NOT NULL, ticket_id TEXT NOT NULL)"
    )
    conn.execute("INSERT INTO plan_tickets(plan_name, ticket_id) VALUES ('old', 't001')")

    row = plan_db.rename_plan(
        conn,
        "old",
        "new",
        materialized_path=".murder/plans/new.md",
    )

    assert row["name"] == "new"
    assert plan_db.get_plan_row(conn, "old") is None
    assert conn.execute("SELECT plan_name FROM plan_revisions").fetchone()["plan_name"] == "new"
    assert (
        conn.execute("SELECT plan_name FROM plan_related_tickets").fetchone()["plan_name"]
        == "new"
    )
    assert conn.execute("SELECT plan_name FROM plan_tickets").fetchone()["plan_name"] == "new"


def test_rename_plan_rejects_missing_and_colliding_names(repo_root) -> None:
    conn = _connect(repo_root)
    _insert_plan(conn, repo_root, _plan("old"))
    _insert_plan(conn, repo_root, _plan("taken"))

    with pytest.raises(KeyError):
        plan_db.rename_plan(
            conn,
            "missing",
            "new",
            materialized_path=".murder/plans/new.md",
        )
    with pytest.raises(ValueError):
        plan_db.rename_plan(
            conn,
            "old",
            "taken",
            materialized_path=".murder/plans/taken.md",
        )


def test_plan_sync_rename_writes_new_markdown_and_removes_old(repo_root) -> None:
    conn = _connect(repo_root)
    plan = _plan("old", body="# Original\n")
    _insert_plan(conn, repo_root, plan)
    write(plan_md(repo_root, "old"), plan)

    row = PlanSync(repo_root, conn).rename_plan("old", "new")

    old_path = plan_md(repo_root, "old")
    new_path = plan_md(repo_root, "new")
    assert row["name"] == "new"
    assert row["materialized_path"] == ".murder/plans/new.md"
    assert not old_path.exists()
    assert parse(new_path.read_text(encoding="utf-8")).name == "new"
    assert parse(new_path.read_text(encoding="utf-8")).frontmatter["owner"] == "planner"
    assert parse(new_path.read_text(encoding="utf-8")).related_tickets == ["t001"]


def test_reconcile_path_only_rename_updates_materialized_path(repo_root) -> None:
    conn = _connect(repo_root)
    plan = _plan("old", body="# Original\n")
    rendered = render(plan)
    _insert_plan(conn, repo_root, plan)
    old_path = plan_md(repo_root, "old")
    new_path = plan_md(repo_root, "moved")
    old_path.parent.mkdir(parents=True, exist_ok=True)
    old_path.write_text(rendered, encoding="utf-8")
    old_path.rename(new_path)

    asyncio.run(PlanSync(repo_root, conn).reconcile_file(new_path))

    row = plan_db.get_plan_row(conn, "old")
    assert row is not None
    assert row["materialized_path"] == ".murder/plans/moved.md"


def test_plan_sync_deprecate_marks_superseded_and_hides_from_active_list(repo_root) -> None:
    conn = _connect(repo_root)
    plan = _plan("old", body="# Original\n")
    _insert_plan(conn, repo_root, plan)
    write(plan_md(repo_root, "old"), plan)

    row = PlanSync(repo_root, conn).deprecate_plan("old")

    old_path = plan_md(repo_root, "old")
    deprecated_path = deprecated_plans_dir(repo_root) / "old.md"
    assert row["name"] == "old"
    assert row["status"] == "superseded"
    assert row["materialized_path"] == ".murder/plans/deprecated_plans/old.md"
    assert not old_path.exists()
    parsed = parse(deprecated_path.read_text(encoding="utf-8"))
    assert parsed.name == "old"
    assert parsed.status == PlanStatus.SUPERSEDED
    assert plan_db.list_plans(conn) == []


def test_retarget_plan_runtime_rekeys_live_planner_and_handler(
    repo_root,
    monkeypatch,
) -> None:
    conn = _connect(repo_root)
    registry = AgentRegistry()
    config = SimpleNamespace(
        project=SimpleNamespace(name="demo"),
        runtime=SimpleNamespace(session_name_template="murder_{project}_{role}{suffix}"),
    )

    def sync_agent(agent) -> None:
        upsert_agent(
            conn,
            agent_id=agent.id,
            role=agent.role.value,
            ticket_id=agent.ticket_id,
            session=agent.session,
            status=agent.status.value,
        )

    rt = SimpleNamespace(db=conn, agents=registry, config=config, sync_agent=sync_agent)
    planner = SimpleNamespace(
        id="planner-old",
        role=AgentRole.PLANNER,
        ticket_id=None,
        status=AgentStatus.RUNNING,
        session="murder_demo_planner_old",
        plan_name="old",
        harness_session=SimpleNamespace(session="murder_demo_planner_old"),
    )
    handler = SimpleNamespace(
        id="planning_handler-old",
        role=AgentRole.PLANNING_HANDLER,
        ticket_id=None,
        status=AgentStatus.RUNNING,
        session="murder_demo_planning_handler_old",
        plan_name="old",
        planner_session="murder_demo_planner_old",
    )
    registry.register(planner)
    registry.register(handler)
    sync_agent(planner)
    sync_agent(handler)
    conn.execute(
        "INSERT INTO agent_messages(agent_id, ordinal, role, body, captured_at) "
        "VALUES ('planner-old', 0, 'user', 'hello', '2026-01-01T00:00:00')"
    )

    calls: list[tuple[str, str]] = []

    async def fake_rename_session(old: str, new: str) -> bool:
        calls.append((old, new))
        return True

    monkeypatch.setattr(
        "murder.orchestration.orchestrator.tmux.rename_session",
        fake_rename_session,
    )

    asyncio.run(Orchestrator(rt)._retarget_plan_runtime("old", "new"))

    assert registry.get_agent("planner-old") is None
    assert registry.get_agent("planner-new") is planner
    assert planner.id == "planner-new"
    assert planner.plan_name == "new"
    assert planner.session == "murder_demo_planner_new"
    assert planner.harness_session.session == "murder_demo_planner_new"
    assert registry.get_agent("planning_handler-new") is handler
    assert handler.id == "planning_handler-new"
    assert handler.plan_name == "new"
    assert handler.planner_session == "murder_demo_planner_new"
    assert calls == [
        ("murder_demo_planner_old", "murder_demo_planner_new"),
        ("murder_demo_planning_handler_old", "murder_demo_planning_handler_new"),
    ]
    rows = conn.execute("SELECT agent_id, session FROM agents ORDER BY agent_id").fetchall()
    assert [(r["agent_id"], r["session"]) for r in rows] == [
        ("planner-new", "murder_demo_planner_new"),
        ("planning_handler-new", "murder_demo_planning_handler_new"),
    ]
    msg_row = conn.execute("SELECT agent_id FROM agent_messages").fetchone()
    assert msg_row["agent_id"] == "planner-new"
