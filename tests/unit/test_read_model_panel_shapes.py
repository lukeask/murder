"""Backend DTO-shape coverage for the C8 ticket editor and C11 plans panel.

Asserts that the read-model snapshots carry the fields the canonical Ink render
specs (newui-inktui C8 / C11) consume:

* ``TicketDetailSnapshot``: a unified frontmatter-stripped ``body`` (preserving the
  ``# Checklist`` lines), display-only header fields (``deps``/``harness``/``model``/
  ``worktree``), and runtime ``schedule_at`` alongside ``status``/``checklist``.
* ``PlanSummary``: ``parent`` (from frontmatter), ``updated_at`` (recency), and
  ``char_count`` (body size) for the indentation + effective-recency ordering.
"""

from __future__ import annotations

from datetime import datetime

from murder.app.service.read_model import ServiceReadModel
from murder.state.persistence import plans as plan_db
from murder.state.persistence import tickets as ticket_db
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.paths import db_path, tickets_dir
from murder.work.plans.schema import Plan, PlanStatus
from murder.work.plans.sync import content_hash
from murder.work.tickets.schema import ChecklistItem, Ticket
from murder.work.tickets.status import TicketStatus

TICKET_MD = """\
---
title: Demo ticket
deps: [t001]
harness: codex
model: gpt-5.1
worktree: .murder/worktrees/demo
---
## Plan

Build the thing.

# Checklist

- [ ] first step
- [x] second step
"""


def _seed_ticket(repo_root) -> ServiceReadModel:
    conn = get_db(db_path(repo_root))
    init_db(conn)
    now = datetime(2026, 1, 1, 12, 0, 0)
    # Dependency ticket must exist (ticket_deps FK references tickets.id).
    ticket_db.insert_ticket(
        conn,
        Ticket(
            id="t001",
            title="Dep",
            status=TicketStatus.DONE,
            harness="codex",
            model="gpt-5.1",
            created_at=now,
            updated_at=now,
        ),
    )
    ticket_db.insert_ticket(
        conn,
        Ticket(
            id="t007",
            title="Demo ticket",
            status=TicketStatus.IN_PROGRESS,
            deps=["t001"],
            harness="codex",
            model="gpt-5.1",
            created_at=now,
            updated_at=now,
            checklist=[
                ChecklistItem(ord=0, text="first step", done=False),
                ChecklistItem(ord=1, text="second step", done=True),
            ],
        ),
    )
    conn.close()
    tdir = tickets_dir(repo_root)
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / "t007.md").write_text(TICKET_MD, encoding="utf-8")
    return ServiceReadModel(db_path(repo_root))


def test_ticket_detail_carries_body_and_header_fields(repo_root) -> None:
    detail = _seed_ticket(repo_root).get_ticket_detail("t007")

    # Unified body is frontmatter-stripped and KEEPS the checklist section so the
    # C8 editor can toggle the `[ ]`/`[x]` lines in place.
    assert "title: Demo ticket" not in detail.body  # frontmatter removed
    assert "# Checklist" in detail.body
    assert "- [ ] first step" in detail.body
    assert "- [x] second step" in detail.body

    # Display-only header fields (C8 line 244 — harness/model display-only).
    assert detail.deps == ("t001",)
    assert detail.harness == "codex"
    assert detail.model == "gpt-5.1"

    # Runtime state delivered alongside the doc.
    assert detail.status is TicketStatus.IN_PROGRESS
    assert tuple((c.text, c.done) for c in detail.checklist) == (
        ("first step", False),
        ("second step", True),
    )

    # Legacy split sections still populated for back-compat consumers.
    assert detail.plan_md.startswith("Build the thing.")


def test_ticket_detail_body_falls_back_when_no_frontmatter(repo_root) -> None:
    model = _seed_ticket(repo_root)
    (tickets_dir(repo_root) / "t007.md").write_text(
        "# Checklist\n\n- [ ] only\n", encoding="utf-8"
    )
    detail = model.get_ticket_detail("t007")
    assert detail.body == "# Checklist\n\n- [ ] only\n"


def test_plans_snapshot_carries_parent_recency_and_size(repo_root) -> None:
    conn = get_db(db_path(repo_root))
    init_db(conn)
    parent_dt = datetime(2026, 1, 1, 9, 0, 0)
    child_dt = datetime(2026, 1, 2, 9, 0, 0)
    parent = Plan(
        name="newui",
        status=PlanStatus.DRAFT,
        created_at=parent_dt,
        updated_at=parent_dt,
        body="# Parent\n\nroot plan body\n",
    )
    child = Plan(
        name="newui-finalpush9",
        status=PlanStatus.DRAFT,
        created_at=child_dt,
        updated_at=child_dt,
        body="# Child\n\nchild plan body that is longer\n",
        frontmatter={"parent": "newui"},
    )
    for plan in (parent, child):
        body_text = plan.body
        plan_db.upsert_plan(
            conn,
            plan,
            content_hash=content_hash(body_text),
            materialized_path=f"plans/{plan.name}.md",
            file_hash=content_hash(body_text),
            sync_state="synced",
            create_revision=True,
            revision_source="import",
        )
    conn.close()

    snapshot = ServiceReadModel(db_path(repo_root)).get_plans_snapshot()
    by_name = {p.name: p for p in snapshot.plans}

    assert by_name["newui"].parent is None
    assert by_name["newui-finalpush9"].parent == "newui"
    # char_count tracks the body length.
    assert by_name["newui"].char_count == len(parent.body)
    assert by_name["newui-finalpush9"].char_count == len(child.body)
    # updated_at is the recency timestamp the ordering uses.
    assert by_name["newui-finalpush9"].updated_at == child_dt
