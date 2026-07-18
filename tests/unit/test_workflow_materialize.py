from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from murder.state.persistence.schema import get_db, init_db
from murder.state.persistence.tickets import compute_ready, update_ticket_status
from murder.state.persistence.workflow_runs import (
    get_workflow_run,
    list_workflow_waits,
)
from murder.state.storage.paths import ticket_md
from murder.work.tickets.parser import parse_ticket
from murder.work.tickets.sync import reconcile_ticket_md
from murder.work.workflows.definition import StageDef, WorkflowDef
from murder.work.workflows.materialize import materialize_workflow
from murder.work.workflows.runtime import (
    ExternalSignalWait,
    StageStatus,
    StaticDagWorkflowStateV1,
    WorkflowStatus,
)


def _conn(repo_root: Path):
    db_file = repo_root / ".murder" / "murder.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(db_file)
    init_db(conn)
    return conn


def _three_stage_workflow() -> WorkflowDef:
    return WorkflowDef(
        name="rewrite-pipeline",
        description="scout then rewrite then plan",
        stages=[
            StageDef(
                id="scout",
                title="Scout the codebase",
                instructions="Survey the area.",
                harness="codex",
                model="gpt-5",
                worktree="shared-tree",
            ),
            StageDef(
                id="rewrite",
                title="Rewrite module",
                instructions="Rewrite based on scout.",
                harness="codex",
                model="gpt-5",
                worktree="shared-tree",
                depends_on=["scout"],
            ),
            StageDef(
                id="plan",
                title="Plan follow-ups",
                instructions="Plan for spec: {spec}",
                harness="cursor",
                model="opus",
                worktree="shared-tree",
                depends_on=["rewrite"],
            ),
        ],
    )


def test_materialize_builds_planned_parent_with_run_record(repo_root: Path) -> None:
    conn = _conn(repo_root)
    result = materialize_workflow(
        conn, repo_root, _three_stage_workflow(), {"spec": "do the thing"}
    )

    parent = conn.execute("SELECT * FROM tickets WHERE id = ?", (result.run_ticket_id,)).fetchone()
    assert parent is not None
    assert parent["status"] == "planned"

    run = get_workflow_run(conn, result.run_ticket_id)
    assert run is not None
    assert run.name == "rewrite-pipeline"
    assert run.workflow_id == result.workflow_id
    assert run.status == WorkflowStatus.WAITING
    assert run.revision == 0
    assert set(run.stage_map) == {"scout", "rewrite", "plan"}
    assert run.stage_map == result.stage_ticket_ids
    # definition_json round-trips the snapshot.
    assert json.loads(run.definition_json)["name"] == "rewrite-pipeline"
    state = StaticDagWorkflowStateV1.model_validate(run.state.value)
    assert [(stage.stage_id, stage.status) for stage in state.stages] == [
        ("scout", StageStatus.READY),
        ("rewrite", StageStatus.BLOCKED),
        ("plan", StageStatus.BLOCKED),
    ]
    waits = list_workflow_waits(conn, run.workflow_id)
    assert {
        wait.spec.correlation_key for wait in waits if isinstance(wait.spec, ExternalSignalWait)
    } == set(result.stage_ticket_ids.values())


def test_materialize_stages_are_ready_with_parent(repo_root: Path) -> None:
    conn = _conn(repo_root)
    result = materialize_workflow(conn, repo_root, _three_stage_workflow(), {"spec": "x"})

    for stage_id, ticket_id in result.stage_ticket_ids.items():
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        assert row is not None, stage_id
        assert row["status"] == "ready", stage_id
        assert row["parent_ticket_id"] == result.run_ticket_id, stage_id


def test_materialize_wires_dependencies(repo_root: Path) -> None:
    conn = _conn(repo_root)
    result = materialize_workflow(conn, repo_root, _three_stage_workflow(), {"spec": "x"})
    scout = result.stage_ticket_ids["scout"]
    rewrite = result.stage_ticket_ids["rewrite"]
    plan = result.stage_ticket_ids["plan"]

    def deps(ticket_id: str) -> set[str]:
        return {
            r["depends_on_id"]
            for r in conn.execute(
                "SELECT depends_on_id FROM ticket_deps WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchall()
        }

    assert deps(scout) == set()
    assert deps(rewrite) == {scout}
    assert deps(plan) == {rewrite}


def test_compute_ready_runs_pipeline_sequentially(repo_root: Path) -> None:
    conn = _conn(repo_root)
    result = materialize_workflow(conn, repo_root, _three_stage_workflow(), {"spec": "x"})
    scout = result.stage_ticket_ids["scout"]
    rewrite = result.stage_ticket_ids["rewrite"]

    # Only the root is runnable; rewrite/plan are ``ready`` but dep-gated.
    assert compute_ready(conn) == [scout]

    # Completing scout unblocks rewrite (proving the scheduler walks the DAG
    # with no extra engine).
    update_ticket_status(conn, scout, "done")
    assert compute_ready(conn) == [rewrite]
    run = get_workflow_run(conn, result.workflow_id)
    assert run is not None
    assert run.revision == 1
    state = StaticDagWorkflowStateV1.model_validate(run.state.value)
    assert [(stage.stage_id, stage.status) for stage in state.stages] == [
        ("scout", StageStatus.SUCCEEDED),
        ("rewrite", StageStatus.READY),
        ("plan", StageStatus.BLOCKED),
    ]

    # At-least-once delivery of the same terminal update is idempotent.
    update_ticket_status(conn, scout, "done")
    assert get_workflow_run(conn, result.workflow_id).revision == 1


def test_ticket_terminal_update_rolls_back_when_signal_cannot_persist(
    repo_root: Path,
) -> None:
    conn = _conn(repo_root)
    result = materialize_workflow(conn, repo_root, _three_stage_workflow(), {"spec": "x"})
    scout = result.stage_ticket_ids["scout"]
    conn.executescript(
        """
        CREATE TRIGGER reject_workflow_signal
        BEFORE INSERT ON workflow_signals
        BEGIN
            SELECT RAISE(ABORT, 'injected signal failure');
        END;
        """
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected signal failure"):
        update_ticket_status(conn, scout, "done")
    assert conn.execute(
        "SELECT status FROM tickets WHERE id = ?",
        (scout,),
    ).fetchone()["status"] == "ready"


def test_placeholder_substitution_and_roundtrip(repo_root: Path) -> None:
    conn = _conn(repo_root)
    result = materialize_workflow(
        conn, repo_root, _three_stage_workflow(), {"spec": "do the thing"}
    )
    plan_ticket = result.stage_ticket_ids["plan"]
    md = ticket_md(repo_root, plan_ticket).read_text(encoding="utf-8")
    parsed = parse_ticket(md, default_title=plan_ticket)

    assert parsed.parse_error is None
    assert "Plan for spec: do the thing" in parsed.body
    assert parsed.worktree == "shared-tree"
    assert parsed.parent == result.run_ticket_id


def test_re_reconcile_preserves_parent(repo_root: Path) -> None:
    conn = _conn(repo_root)
    result = materialize_workflow(conn, repo_root, _three_stage_workflow(), {"spec": "x"})
    rewrite = result.stage_ticket_ids["rewrite"]

    # Simulate the TicketSync poll re-reconciling the on-disk file: the parent
    # link must survive (no clobber).
    reconcile_ticket_md(conn=conn, repo_root=repo_root, ticket_id=rewrite)
    row = conn.execute("SELECT parent_ticket_id FROM tickets WHERE id = ?", (rewrite,)).fetchone()
    assert row["parent_ticket_id"] == result.run_ticket_id


def test_invalid_workflow_raises(repo_root: Path) -> None:
    conn = _conn(repo_root)
    bad = WorkflowDef(name="bad", stages=[])  # no stages
    with pytest.raises(ValueError):
        materialize_workflow(conn, repo_root, bad)


def test_run_ticket_title_has_no_machine_prefix(repo_root: Path) -> None:
    # The parent run ticket's title is recovered from its leading heading; it
    # must read as a clean human title, not leak a "workflow:" token.
    conn = _conn(repo_root)
    result = materialize_workflow(conn, repo_root, _three_stage_workflow(), {"spec": "x"})
    row = conn.execute("SELECT title FROM tickets WHERE id = ?", (result.run_ticket_id,)).fetchone()
    assert row["title"] == "Workflow: rewrite-pipeline"


def test_materialize_without_args(repo_root: Path) -> None:
    # args=None must materialize cleanly; unfilled {placeholder} tokens survive
    # verbatim into the stage brief rather than crashing.
    conn = _conn(repo_root)
    result = materialize_workflow(conn, repo_root, _three_stage_workflow())
    assert set(result.stage_ticket_ids) == {"scout", "rewrite", "plan"}
    plan_ticket = result.stage_ticket_ids["plan"]
    md = ticket_md(repo_root, plan_ticket).read_text(encoding="utf-8")
    parsed = parse_ticket(md, default_title=plan_ticket)
    assert parsed.parse_error is None
    assert "Plan for spec: {spec}" in parsed.body


def test_failed_materialize_leaves_no_orphans(repo_root: Path) -> None:
    # If a step raises after some stage tickets are written, the partial tree is
    # torn down: no orphan ticket rows and no workflow_runs anchor.
    import murder.work.workflows.materialize as m

    conn = _conn(repo_root)
    defn = _three_stage_workflow()

    calls = {"n": 0}
    real_insert = m.insert_workflow_run

    def boom(*a, **k):
        raise RuntimeError("simulated failure at run-record insert")

    m.insert_workflow_run = boom
    try:
        with pytest.raises(RuntimeError):
            materialize_workflow(conn, repo_root, defn, {"spec": "x"})
    finally:
        m.insert_workflow_run = real_insert

    assert conn.execute("SELECT COUNT(*) AS c FROM tickets").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM workflow_runs").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM ticket_deps").fetchone()["c"] == 0
    leftover = list((repo_root / ".murder" / "tickets").glob("*.md"))
    assert leftover == [], leftover
