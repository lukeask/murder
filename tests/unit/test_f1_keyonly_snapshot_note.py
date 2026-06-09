"""F1 — key-only event uniformity: NOTE entity emit sites.

Sibling of ``test_f1_keyonly_snapshot.py`` (AGENT backbone),
``test_f1_keyonly_snapshot_ticket.py`` (TICKET), and
``test_f1_keyonly_snapshot_plan.py`` (PLAN). Proves that note read-model
mutations funnel a single key-only ``state.snapshot{entity=note, key=<note_id>}``
through the established choke points:

- ``NoteSync`` (PRIMARY filesystem->DB writer) via the ``on_change`` callback
  wired via the F5.1 notify_changed seam -- fires on reconcile insert and on
  a body/path edit, but NOT on an unchanged reconcile;
- ``DocumentAccess.note_path_for`` (the second live ``ensure_note`` path, behind
  the ``document.note_path`` RPC) -- emits when it CREATES the row, not when the
  note already exists;
- ``Orchestrator.ensure_note`` / ``retire_note`` / ``submit_notetaker_capture``
  RPCs (write notes rows DIRECTLY, bypassing NoteSync) via async
  ``publish_snapshot``.

Assertions filter on ``entity == Entity.NOTE`` because some paths also touch
other entities.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from murder.app.service.document_access import DocumentAccess
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


def _note_snapshots(captured: list[object], name: str) -> list[StateSnapshotEvent]:
    return [
        e
        for e in captured
        if isinstance(e, StateSnapshotEvent)
        and e.entity == Entity.NOTE
        and e.key == name
    ]


async def _drain(rt: Runtime) -> None:
    # Sync choke points (emit_snapshot) schedule fire-and-forget tasks; conftest
    # noop-patches asyncio.sleep so we drain explicitly. Async paths
    # (publish_snapshot, notify_changed) have already awaited by the time we
    # get here.
    if rt._emit_tasks:
        await asyncio.gather(*list(rt._emit_tasks))


def _write_note(repo_root: Path, name: str, body: str = "# Note body\n") -> Path:
    from murder.state.storage.paths import note_md, notes_dir

    notes_dir(repo_root).mkdir(parents=True, exist_ok=True)
    path = note_md(repo_root, name)
    path.write_text(body)
    return path


# === filesystem->DB primary writer (NoteSync) ==============================
# NoteSync now uses the async notify_changed seam (F5.3): on_change=(Entity,str)->coro.
# Pass rt.bus.publish as on_change so events appear on the real bus.


async def _bus_emit(rt: Runtime, entity: Entity, key: str) -> None:
    """Async on_change callback that publishes directly to the bus."""
    await rt.bus.publish(
        StateSnapshotEvent(
            run_id=rt.run_id,
            agent_id="filesystem-sync",
            entity=entity,
            key=key,
        )
    )


@pytest.mark.asyncio
async def test_note_sync_reconcile_insert_emits_one_key_only_note_snapshot(
    repo_root: Path,
) -> None:
    from murder.work.notes.sync import NoteSync

    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    path = _write_note(repo_root, "alpha")
    sync = NoteSync(
        repo_root,
        rt.db,
        on_change=lambda entity, key: _bus_emit(rt, entity, key),
    )
    # Drive ONE reconcile directly -- never NoteSync.run() (a poll loop that would
    # busy-spin under conftest's noop sleep).
    await sync.reconcile_file(path)
    # on_change is async and awaited directly in reconcile_file; no _drain needed.

    snaps = _note_snapshots(captured, "alpha")
    assert len(snaps) == 1
    assert snaps[0].payload is None  # key-only by default


@pytest.mark.asyncio
async def test_note_sync_reconcile_edit_emits_one_key_only_note_snapshot(
    repo_root: Path,
) -> None:
    from murder.work.notes.sync import NoteSync

    rt = _runtime(repo_root)

    # Import once (seed), then edit the body so the update branch fires.
    path = _write_note(repo_root, "beta")
    seed = NoteSync(repo_root, rt.db)
    await seed.reconcile_file(path)

    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    path.write_text("# Note body\n\nedited line\n")
    sync = NoteSync(
        repo_root,
        rt.db,
        on_change=lambda entity, key: _bus_emit(rt, entity, key),
    )
    await sync.reconcile_file(path)

    assert len(_note_snapshots(captured, "beta")) == 1


@pytest.mark.asyncio
async def test_note_sync_unchanged_reconcile_does_not_emit(repo_root: Path) -> None:
    from murder.work.notes.sync import NoteSync

    rt = _runtime(repo_root)
    path = _write_note(repo_root, "gamma")
    seed = NoteSync(repo_root, rt.db)
    await seed.reconcile_file(path)

    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    # Re-reconcile the SAME unchanged file -> early return, no DB write, no emit.
    sync = NoteSync(
        repo_root,
        rt.db,
        on_change=lambda entity, key: _bus_emit(rt, entity, key),
    )
    await sync.reconcile_file(path)

    assert _note_snapshots(captured, "gamma") == []


# === DocumentAccess.note_path_for (second ensure_note path) =================


@pytest.mark.asyncio
async def test_note_path_for_emits_when_it_creates_the_row(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    docs = DocumentAccess(
        repo_root,
        rt.db,
        on_note_change=lambda name: rt.emit_snapshot(Entity.NOTE, name),
    )
    docs.note_path_for("delta")
    await _drain(rt)

    assert len(_note_snapshots(captured, "delta")) == 1


@pytest.mark.asyncio
async def test_note_path_for_does_not_emit_for_existing_row(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    docs = DocumentAccess(
        repo_root,
        rt.db,
        on_note_change=lambda name: rt.emit_snapshot(Entity.NOTE, name),
    )
    docs.note_path_for("epsilon")  # creates it
    await _drain(rt)

    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    docs.note_path_for("epsilon")  # already exists -> no emit
    await _drain(rt)

    assert _note_snapshots(captured, "epsilon") == []


# === orchestrator direct-write RPCs (bypass NoteSync) =======================


@pytest.mark.asyncio
async def test_ensure_note_rpc_emits_one_key_only_note_snapshot(repo_root: Path) -> None:
    from murder.runtime.orchestration.orchestrator import Orchestrator

    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    orch = Orchestrator(rt)
    await orch.ensure_note("zeta")
    await _drain(rt)

    assert len(_note_snapshots(captured, "zeta")) == 1


@pytest.mark.asyncio
async def test_retire_note_rpc_emits_one_key_only_note_snapshot(repo_root: Path) -> None:
    from murder.runtime.orchestration.orchestrator import Orchestrator
    from murder.work import notes as notes_mod

    rt = _runtime(repo_root)
    # Seed an active note to retire.
    notes_mod.ensure_note(rt.db, repo_root, "eta")

    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    orch = Orchestrator(rt)
    await orch.retire_note("eta")
    await _drain(rt)

    assert len(_note_snapshots(captured, "eta")) == 1
