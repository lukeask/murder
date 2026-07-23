"""F3b — plan.create data-integrity guard on name conflict.

``plan.create`` is a thin wrap over ``scaffold_plan``, which UPSERTs — so without
a guard, creating a plan with an existing live plan's name would silently clobber
that plan's body. The user's top priority: a plan-name conflict must NEVER
overwrite or destroy existing plan data.

Rules under test (mirroring notes' status-aware ``active_note_name_exists``):
  1. Name owned by a LIVE plan (status draft/accepted) -> reject, never overwrite.
  2. Name owned only by a SUPERSEDED plan -> allow, old plan's data preserved
     (the superseded row is archived to a fresh key via rename_plan).
  3. No collision -> normal create.

Harness mirrors test_f3_ticket_plan_rpcs.py (real Runtime + sqlite + OrchestrationNotifier; conftest
noop-patches asyncio.sleep so no poll loop spins).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from murder.app.service.runtime import Runtime
from murder.runtime.orchestration.notifier import OrchestrationNotifier
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.state.persistence.plans import live_plan_name_exists
from murder.state.persistence.runs import insert_run
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.paths import plan_md


def _config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


def _runtime(repo_root: Path) -> Runtime:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    rt = Runtime(_config(), repo_root)
    rt.db = conn
    rt.run_id = "run-test"
    insert_run(conn, rt.run_id, "{}")
    rt.bus = OrchestrationNotifier(conn)
    return rt


def _orch(rt: Runtime):
    from murder.runtime.orchestration.orchestrator import Orchestrator

    return Orchestrator(rt)


async def _drain(rt: Runtime) -> None:
    if rt._emit_tasks:
        await asyncio.gather(*list(rt._emit_tasks))


def _row(rt: Runtime, name: str):
    r = rt.db.execute(
        "SELECT name, status, body FROM plans WHERE name = ?", (name,)
    ).fetchone()
    return dict(r) if r else None


def _set_status(rt: Runtime, name: str, status: str) -> None:
    rt.db.execute("UPDATE plans SET status = ? WHERE name = ?", (status, name))
    rt.db.commit()


def _set_body(rt: Runtime, name: str, body: str) -> None:
    rt.db.execute("UPDATE plans SET body = ? WHERE name = ?", (body, name))
    rt.db.commit()


# === guard helper ============================================================


@pytest.mark.asyncio
async def test_live_plan_name_exists_status_aware(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    await _orch(rt).create_plan("p", "")
    await _drain(rt)
    # Fresh scaffold defaults to draft -> live -> name owned.
    assert live_plan_name_exists(rt.db, "p") is True
    _set_status(rt, "p", "accepted")
    assert live_plan_name_exists(rt.db, "p") is True
    _set_status(rt, "p", "superseded")
    assert live_plan_name_exists(rt.db, "p") is False
    # Unknown name -> not owned.
    assert live_plan_name_exists(rt.db, "nope") is False


# === path 3: no collision ====================================================


@pytest.mark.asyncio
async def test_create_no_collision_succeeds(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    result = await _orch(rt).create_plan("fresh-plan", "")
    await _drain(rt)
    assert result["handled"] is True
    assert result["plan_name"] == "fresh-plan"
    assert plan_md(repo_root, "fresh-plan").exists()
    assert _row(rt, "fresh-plan") is not None


# === path 1: live-name collision -> reject, data intact ======================


@pytest.mark.asyncio
async def test_create_over_live_plan_rejected_data_intact(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    orch = _orch(rt)
    await orch.create_plan("keep", "")
    await _drain(rt)
    # Give the live plan a distinctive body the scaffold would otherwise clobber.
    _set_body(rt, "keep", "PRECIOUS ORIGINAL BODY")

    with pytest.raises(FileExistsError):
        await orch.create_plan("keep", "")

    # The live plan's data is fully intact — never overwritten.
    row = _row(rt, "keep")
    assert row is not None
    assert row["status"] == "draft"
    assert row["body"] == "PRECIOUS ORIGINAL BODY"
    # No archived/duplicate row was created.
    count = rt.db.execute("SELECT COUNT(*) AS c FROM plans WHERE name LIKE 'keep%'").fetchone()["c"]
    assert count == 1


@pytest.mark.asyncio
async def test_create_over_accepted_plan_also_rejected(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    orch = _orch(rt)
    await orch.create_plan("live-accepted", "")
    await _drain(rt)
    _set_status(rt, "live-accepted", "accepted")
    _set_body(rt, "live-accepted", "ACCEPTED BODY")

    with pytest.raises(FileExistsError):
        await orch.create_plan("live-accepted", "")

    row = _row(rt, "live-accepted")
    assert row["status"] == "accepted"
    assert row["body"] == "ACCEPTED BODY"


# === path 2: superseded-name reuse -> succeeds, old data preserved ===========


@pytest.mark.asyncio
async def test_create_over_superseded_succeeds_old_data_preserved(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    orch = _orch(rt)
    await orch.create_plan("recycle", "")
    await _drain(rt)
    _set_body(rt, "recycle", "OLD SUPERSEDED BODY")
    _set_status(rt, "recycle", "superseded")
    # A revision row exists for the original plan (scaffold creates one).
    old_revs = rt.db.execute(
        "SELECT COUNT(*) AS c FROM plan_revisions WHERE plan_name = ?", ("recycle",)
    ).fetchone()["c"]
    assert old_revs >= 1

    result = await orch.create_plan("recycle", "")
    await _drain(rt)

    # New plan took the name and is live (draft) with a fresh scaffold body.
    assert result["handled"] is True
    new_row = _row(rt, "recycle")
    assert new_row["status"] == "draft"
    assert new_row["body"] != "OLD SUPERSEDED BODY"

    # The old plan's data is fully preserved under an archived key.
    archived = rt.db.execute(
        "SELECT name, status, body FROM plans WHERE name LIKE 'recycle-superseded%'"
    ).fetchone()
    assert archived is not None
    assert archived["status"] == "superseded"
    assert archived["body"] == "OLD SUPERSEDED BODY"
    # Its revision history followed it (rename_plan moves child references).
    moved_revs = rt.db.execute(
        "SELECT COUNT(*) AS c FROM plan_revisions WHERE plan_name = ?", (archived["name"],)
    ).fetchone()["c"]
    assert moved_revs == old_revs

    # Exactly two rows now share the recycle prefix: the new live one + archive.
    count = rt.db.execute(
        "SELECT COUNT(*) AS c FROM plans WHERE name LIKE 'recycle%'"
    ).fetchone()["c"]
    assert count == 2


@pytest.mark.asyncio
async def test_create_over_superseded_twice_archives_uniquely(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    orch = _orch(rt)
    # First superseded incarnation.
    await orch.create_plan("multi", "")
    await _drain(rt)
    _set_body(rt, "multi", "BODY ONE")
    _set_status(rt, "multi", "superseded")

    await orch.create_plan("multi", "")  # archives BODY ONE -> multi-superseded
    await _drain(rt)
    _set_body(rt, "multi", "BODY TWO")
    _set_status(rt, "multi", "superseded")

    await orch.create_plan("multi", "")  # archives BODY TWO -> multi-superseded-2
    await _drain(rt)

    bodies = {
        str(r["body"])
        for r in rt.db.execute(
            "SELECT body FROM plans WHERE name LIKE 'multi-superseded%'"
        ).fetchall()
    }
    assert bodies == {"BODY ONE", "BODY TWO"}
    # No data lost; collision-safe suffixing kept both archives distinct.
    assert rt.db.execute(
        "SELECT COUNT(*) AS c FROM plans WHERE name = 'multi'"
    ).fetchone()["c"] == 1
