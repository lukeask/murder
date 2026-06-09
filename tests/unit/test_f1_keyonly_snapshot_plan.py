"""F1 — key-only event uniformity: PLAN entity emit sites.

Sibling of ``test_f1_keyonly_snapshot.py`` (AGENT backbone) and
``test_f1_keyonly_snapshot_ticket.py`` (TICKET). Proves that plan read-model
mutations funnel a single key-only ``state.snapshot{entity=plan, key=<plan_id>}``
through the established choke points:

- ``PlanSync`` (PRIMARY filesystem->DB writer) via the injected ``on_plan_change``
  callback wired to ``Runtime.emit_snapshot`` -- fires on reconcile insert/ingest,
  rename (BOTH old + new key), deprecate, and parse-error sync_state flip;
- ``Orchestrator.scaffold_plan`` (writes plans rows DIRECTLY, bypassing PlanSync)
  via async ``publish_snapshot``;
- ``Orchestrator._record_user_block`` for a ``planner-{name}`` agent (plans list
  reorders by planner-message recency without any plans-table write).

Assertions filter on ``entity == Entity.PLAN`` because some paths also touch
agents -> ``sync_agent`` -> AGENT snapshots.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from murder.app.service.runtime import Runtime
from murder.bus import Bus
from murder.bus.protocol import Entity, StateSnapshotEvent
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.state.persistence.schema import get_db, init_db


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
    rt.bus = Bus(rt.run_id, conn)
    return rt


async def _record(sink: list[object], ev: object) -> None:
    sink.append(ev)


def _plan_snapshots(captured: list[object], name: str) -> list[StateSnapshotEvent]:
    return [
        e
        for e in captured
        if isinstance(e, StateSnapshotEvent)
        and e.entity == Entity.PLAN
        and e.key == name
    ]


async def _drain(rt: Runtime) -> None:
    # Sync choke points (emit_snapshot) schedule fire-and-forget tasks; conftest
    # noop-patches asyncio.sleep so we drain explicitly. Async paths
    # (publish_snapshot) have already awaited by the time we get here.
    if rt._emit_tasks:
        await asyncio.gather(*list(rt._emit_tasks))


def _write_plan(repo_root: Path, name: str, body: str = "# Plan body\n") -> Path:
    from murder.state.storage.paths import plan_md, plans_dir

    plans_dir(repo_root).mkdir(parents=True, exist_ok=True)
    path = plan_md(repo_root, name)
    path.write_text(f"---\nname: {name}\nstatus: draft\n---\n\n{body}")
    return path


# === filesystem->DB primary writer (PlanSync) ==============================


@pytest.mark.asyncio
async def test_plan_sync_reconcile_emits_one_key_only_plan_snapshot(
    repo_root: Path,
) -> None:
    from murder.work.plans.sync import PlanSync

    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    path = _write_plan(repo_root, "alpha")
    sync = PlanSync(
        repo_root,
        rt.db,
        on_plan_change=lambda name: rt.emit_snapshot(Entity.PLAN, name),
    )
    # Drive ONE reconcile directly -- never PlanSync.run() (a poll loop that
    # would busy-spin under conftest's noop sleep).
    await sync.reconcile_file(path)
    await _drain(rt)

    assert len(_plan_snapshots(captured, "alpha")) == 1


@pytest.mark.asyncio
async def test_plan_sync_parse_error_emits_for_existing_row(repo_root: Path) -> None:
    from murder.work.plans.sync import PlanSync

    rt = _runtime(repo_root)

    # First import a valid plan so the parse-error branch (which requires the row
    # to already exist) is exercised.
    path = _write_plan(repo_root, "beta")
    seed = PlanSync(repo_root, rt.db)
    await seed.reconcile_file(path)

    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    # Now corrupt the file: sync_state flips to parse_error (a visible badge), so
    # this DOES emit (unlike the ticket parse-error case, which has no row badge).
    path.write_text("---\nnot: [valid yaml\n---\n# broken\n")
    sync = PlanSync(
        repo_root,
        rt.db,
        on_plan_change=lambda name: rt.emit_snapshot(Entity.PLAN, name),
    )
    await sync.reconcile_file(path)
    await _drain(rt)

    assert len(_plan_snapshots(captured, "beta")) == 1


@pytest.mark.asyncio
async def test_plan_sync_rename_emits_old_and_new_keys(repo_root: Path) -> None:
    from murder.work.plans.sync import PlanSync

    rt = _runtime(repo_root)
    seed = PlanSync(repo_root, rt.db)
    await seed.reconcile_file(_write_plan(repo_root, "gamma"))

    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    sync = PlanSync(
        repo_root,
        rt.db,
        on_plan_change=lambda name: rt.emit_snapshot(Entity.PLAN, name),
    )
    sync.rename_plan("gamma", "gamma-renamed")
    await _drain(rt)

    # Both keys: old leaves the list, new appears.
    assert len(_plan_snapshots(captured, "gamma")) == 1
    assert len(_plan_snapshots(captured, "gamma-renamed")) == 1


@pytest.mark.asyncio
async def test_plan_sync_deprecate_emits_one_key_only_plan_snapshot(
    repo_root: Path,
) -> None:
    from murder.work.plans.sync import PlanSync

    rt = _runtime(repo_root)
    seed = PlanSync(repo_root, rt.db)
    await seed.reconcile_file(_write_plan(repo_root, "delta"))

    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    sync = PlanSync(
        repo_root,
        rt.db,
        on_plan_change=lambda name: rt.emit_snapshot(Entity.PLAN, name),
    )
    sync.deprecate_plan("delta")
    await _drain(rt)

    assert len(_plan_snapshots(captured, "delta")) == 1


# === orchestrator scaffold (direct DB write, bypasses PlanSync) =============


@pytest.mark.asyncio
async def test_scaffold_plan_emits_one_key_only_plan_snapshot(repo_root: Path) -> None:
    from murder.runtime.orchestration.orchestrator import Orchestrator

    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    orch = Orchestrator(rt)
    await orch.scaffold_plan("epsilon", "# Body\n")
    await _drain(rt)

    snaps = _plan_snapshots(captured, "epsilon")
    assert len(snaps) == 1
    assert snaps[0].payload is None  # key-only by default


# === planner chat-send reorders the plans list =============================


@pytest.mark.asyncio
async def test_record_user_block_for_planner_emits_plan_snapshot(
    repo_root: Path,
) -> None:
    from murder.runtime.orchestration.orchestrator import Orchestrator

    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    orch = Orchestrator(rt)
    await orch._record_user_block("planner-zeta", "please revise section 3")
    await _drain(rt)

    assert len(_plan_snapshots(captured, "zeta")) == 1


@pytest.mark.asyncio
async def test_record_user_block_for_non_planner_does_not_emit_plan(
    repo_root: Path,
) -> None:
    from murder.runtime.orchestration.orchestrator import Orchestrator

    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    orch = Orchestrator(rt)
    await orch._record_user_block("crow-t001", "hi")
    await _drain(rt)

    plan_snaps = [e for e in captured if isinstance(e, StateSnapshotEvent) and e.entity == Entity.PLAN]
    assert plan_snaps == []
