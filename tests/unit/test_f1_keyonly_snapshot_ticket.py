"""F1 — key-only event uniformity: TICKET entity emit sites.

Sibling of ``test_f1_keyonly_snapshot.py`` (the AGENT backbone). Proves that
ticket read-model mutations funnel a single key-only
``state.snapshot{entity=ticket, key=<ticket_id>}`` through the established choke
points, alongside (not replacing) any existing typed ``StatusChangeEvent``:

- ``Orchestrator._emit_ticket_status`` (status-transition choke point: kickoff /
  retry / force / carve-ready, and ``outcome.fail_ticket`` via the injected
  ``emit_status``);
- ``TicketSync.reconcile_path`` (PRIMARY filesystem->DB writer) via the injected
  ``on_ticket_change`` callback wired to ``Runtime.emit_snapshot``;
- ``TicketOutcomeService.block_ticket`` (a status path with no typed event).

All assertions filter on ``entity == Entity.TICKET`` because these paths also
reap crow agents -> ``sync_agent`` -> AGENT snapshots in the same call.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from murder.app.service.runtime import Runtime
from murder.bus import Bus
from murder.bus.protocol import Entity, StateSnapshotEvent, StatusChangeEvent
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.state.persistence.runs import insert_run
from murder.state.persistence.schema import get_db, init_db
from murder.work.tickets.status import TicketStatus


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
    rt.bus = Bus(rt.run_id, conn)
    return rt


async def _record(sink: list[object], ev: object) -> None:
    sink.append(ev)


def _ticket_snapshots(captured: list[object], ticket_id: str) -> list[StateSnapshotEvent]:
    return [
        e
        for e in captured
        if isinstance(e, StateSnapshotEvent)
        and e.entity == Entity.TICKET
        and e.key == ticket_id
    ]


async def _drain(rt: Runtime) -> None:
    # Sync choke points (emit_snapshot) schedule fire-and-forget tasks; conftest
    # noop-patches asyncio.sleep so we drain explicitly. Async paths
    # (publish_snapshot) have already awaited by the time we get here.
    if rt._emit_tasks:
        await asyncio.gather(*list(rt._emit_tasks))


# === status-transition choke point ==========================================


@pytest.mark.asyncio
async def test_emit_ticket_status_emits_one_key_only_ticket_snapshot(
    repo_root: Path,
) -> None:
    from murder.runtime.orchestration.orchestrator import Orchestrator

    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    orch = Orchestrator(rt)
    await orch._emit_ticket_status("t007", TicketStatus.READY, TicketStatus.IN_PROGRESS.value)
    await _drain(rt)

    snaps = _ticket_snapshots(captured, "t007")
    assert len(snaps) == 1
    assert snaps[0].payload is None  # key-only by default
    # The existing typed StatusChangeEvent is preserved, not replaced.
    status_events = [
        e for e in captured if isinstance(e, StatusChangeEvent) and e.entity_id == "t007"
    ]
    assert len(status_events) == 1


# === filesystem->DB primary writer (TicketSync) =============================


@pytest.mark.asyncio
async def test_ticket_sync_reconcile_emits_one_key_only_ticket_snapshot(
    repo_root: Path,
) -> None:
    from murder.state.storage.paths import ticket_md, tickets_dir
    from murder.work.tickets.sync import TicketSync

    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    tickets_dir(repo_root).mkdir(parents=True, exist_ok=True)
    path = ticket_md(repo_root, "t042")
    path.write_text(
        "---\ntitle: A ticket\nharness: codex\nmodel: gpt-5\n---\n"
        "# A ticket\n\n## Plan\n\n## Working Notes\n"
    )

    sync = TicketSync(
        repo_root,
        rt.db,
        on_ticket_change=lambda tid: rt.emit_snapshot(Entity.TICKET, tid),
    )
    # Drive ONE reconcile directly — never TicketSync.run() (a poll loop that
    # would busy-spin under conftest's noop sleep).
    sync.reconcile_path(path)
    await _drain(rt)

    assert len(_ticket_snapshots(captured, "t042")) == 1


@pytest.mark.asyncio
async def test_ticket_sync_parse_error_does_not_emit(repo_root: Path) -> None:
    from murder.state.storage.paths import ticket_md, tickets_dir
    from murder.work.tickets.sync import TicketSync

    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    tickets_dir(repo_root).mkdir(parents=True, exist_ok=True)
    # Pre-existing ticket so the parse-error branch (which requires the ticket to
    # already exist) is taken; bad frontmatter triggers the early return.
    from tests.unit.test_ticket_sync_unified import _insert_ticket

    _insert_ticket(rt.db, "t099")
    path = ticket_md(repo_root, "t099")
    path.write_text("---\nnot: [valid yaml\n---\n# broken\n")

    sync = TicketSync(
        repo_root,
        rt.db,
        on_ticket_change=lambda tid: rt.emit_snapshot(Entity.TICKET, tid),
    )
    err = sync.reconcile_path(path)
    await _drain(rt)

    assert err is not None  # parse failed -> early return before COMMIT
    assert _ticket_snapshots(captured, "t099") == []


# === block path (no typed event) ============================================


@pytest.mark.asyncio
async def test_block_ticket_emits_one_key_only_ticket_snapshot(repo_root: Path) -> None:
    from murder.runtime.orchestration.outcome import TicketOutcomeService

    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    from tests.unit.test_ticket_sync_unified import _insert_ticket

    _insert_ticket(rt.db, "t055", status="in_progress")

    class _FakeEscalations:
        async def record_ticket_failure(self, ticket_id: str, reason: str) -> None:
            return None

    async def _emit_status(*_args: object) -> None:  # unused on block path
        raise AssertionError("block_ticket must not call emit_status")

    svc = TicketOutcomeService(
        conn=rt.db,
        repo_root=repo_root,
        escalations=_FakeEscalations(),  # type: ignore[arg-type]
        emit_status=_emit_status,
        emit_snapshot=lambda tid: rt.publish_snapshot(Entity.TICKET, tid),
    )
    await svc.block_ticket("t055", "blocked for review")
    await _drain(rt)

    assert len(_ticket_snapshots(captured, "t055")) == 1
