"""F11 H2 — Ticket-status emit-site coverage audit.

Centerpiece of H2: a row-by-row walk of the F1 coverage table's TICKET section
(v0push § "F1 coverage audit"), asserting every ticket-status
mutation that runs *inside the service* funnels its key-only
``state.snapshot{entity=ticket, key=<ticket_id>}`` through the established choke
points. The four sites the review flagged as "no typed event today" are the
focus; this module proves each now emits (or documents why it cannot).

Coverage split (deliberate — see advisor notes):

PARAMETRIZED EMIT MATRIX (the 4 in-service sites that emit on a live bus):
  - ``orchestrator.reopen_ticket``        (done→planned cascade; was a gap)
  - ``outcome.block_ticket``              (no StatusChangeEvent; emits snapshot)
  - ``coordinator._block_ticket``         (no StatusChangeEvent; emits snapshot)
  - ``sync.reconcile_path`` callback      (PRIMARY filesystem→DB writer)

DOCUMENTED-BEHAVIOR SITES (cannot emit at their call point — proven by behavior,
not by a vacuous "no event" assert):
  - ``recovery.reconcile_agents_vs_tmux`` — runs in ``Runtime.start()`` *before*
    ``self.run_id``/``self.bus`` exist (runtime.py:109 vs :117/:120), so there is
    no bus to publish on. Ink pulls a fresh snapshot on connect and nothing is
    connected before the bus is created, so no emit is needed or possible.
  - ``service_cmd.cmd_retry`` — runs in a SEPARATE CLI process with its own
    SQLite connection and NO bus client. It writes the DB row only (not the
    ``.md`` file), so neither the service bus nor the filesystem→DB watcher sees
    it; there is no service-side poll that re-reads ticket status. The change
    surfaces on the next client reconnect (fresh snapshot pull) — the same
    safety logic as recovery. Wiring an emit here would require IPC the plan
    forbids. Recorded as a residual gap (see report).

This module reuses the established fake-bus idiom from
``test_f1_keyonly_snapshot_ticket.py``: subscribe a recorder to a real ``Bus``,
filter captured events to ``Entity.TICKET``, and drain the sync emit tasks.
All paths are driven directly — never via a ``.run()`` poll loop (conftest
noop-patches ``asyncio.sleep``; a loop would busy-spin).
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
from murder.state.persistence.runs import insert_run
from murder.state.persistence.schema import get_db, init_db
from murder.work.tickets.status import TicketStatus

from tests.unit.test_ticket_sync_unified import _insert_ticket


# === shared fixtures (reuse the F1 ticket-snapshot idiom) ====================


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
    # Sync emit_snapshot schedules fire-and-forget tasks; conftest noop-patches
    # asyncio.sleep so we drain explicitly. Async publish_snapshot has already
    # awaited by the time we return here.
    if rt._emit_tasks:
        await asyncio.gather(*list(rt._emit_tasks))


# === parametrized emit matrix (4 in-service sites) ===========================


async def _drive_reopen(rt: Runtime, repo_root: Path, tid: str) -> None:
    from murder.runtime.orchestration.orchestrator import Orchestrator

    # done → planned cascade. reopen reaps crow agents (no-op here — none
    # registered) then publishes a ticket snapshot per status-changed ticket.
    _insert_ticket(rt.db, tid, status="done")
    orch = Orchestrator(rt)
    await orch.reopen_ticket(tid)


async def _drive_outcome_block(rt: Runtime, repo_root: Path, tid: str) -> None:
    from murder.runtime.orchestration.outcome import TicketOutcomeService

    _insert_ticket(rt.db, tid, status="in_progress")

    class _FakeEscalations:
        async def record_ticket_failure(self, ticket_id: str, reason: str) -> None:
            return None

    async def _emit_status(*_a: object) -> None:  # unused on block path
        raise AssertionError("block_ticket must not call emit_status")

    svc = TicketOutcomeService(
        conn=rt.db,
        repo_root=repo_root,
        escalations=_FakeEscalations(),  # type: ignore[arg-type]
        emit_status=_emit_status,
        emit_snapshot=lambda t: rt.publish_snapshot(Entity.TICKET, t),
    )
    await svc.block_ticket(tid, "blocked for review")


async def _drive_coordinator_block(rt: Runtime, repo_root: Path, tid: str) -> None:
    from murder.verdict.completion.coordinator import CompletionCoordinator
    from murder.verdict.completion.registry import CheckRegistry

    _insert_ticket(rt.db, tid, status="in_progress")
    coord = CompletionCoordinator(rt, CheckRegistry())
    await coord._block_ticket(tid)


async def _drive_sync_reconcile(rt: Runtime, repo_root: Path, tid: str) -> None:
    from murder.state.storage.paths import ticket_md, tickets_dir
    from murder.work.tickets.sync import TicketSync

    tickets_dir(repo_root).mkdir(parents=True, exist_ok=True)
    path = ticket_md(repo_root, tid)
    path.write_text(
        "---\ntitle: A ticket\nharness: codex\nmodel: gpt-5\n---\n"
        "# A ticket\n\n## Plan\n\n## Working Notes\n"
    )
    sync = TicketSync(
        repo_root,
        rt.db,
        on_ticket_change=lambda t: rt.emit_snapshot(Entity.TICKET, t),
    )
    # ONE reconcile, never TicketSync.run() (poll loop -> busy-spin).
    sync.reconcile_path(path)


@pytest.mark.parametrize(
    ("site", "driver", "tid"),
    [
        ("orchestrator.reopen_ticket", _drive_reopen, "t101"),
        ("outcome.block_ticket", _drive_outcome_block, "t102"),
        ("coordinator._block_ticket", _drive_coordinator_block, "t103"),
        ("sync.reconcile_path", _drive_sync_reconcile, "t104"),
    ],
)
@pytest.mark.asyncio
async def test_ticket_status_mutation_emits_key_only_ticket_snapshot(
    repo_root: Path, site: str, driver, tid: str
) -> None:
    """Every in-service ticket-status mutation fires exactly one key-only
    ``state.snapshot{ticket}`` for the mutated ticket, so a connected Ink client
    refetches the schedule snapshot (moving the ticket between buckets) without a
    manual refresh."""
    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    await driver(rt, repo_root, tid)
    await _drain(rt)

    snaps = _ticket_snapshots(captured, tid)
    assert len(snaps) == 1, f"{site}: expected one key-only ticket snapshot, got {snaps}"
    assert snaps[0].payload is None, f"{site}: snapshot must be key-only"


# === reopen CASCADE (the actual gap: every cascaded ticket must emit) ========


@pytest.mark.asyncio
async def test_reopen_cascade_emits_key_only_snapshot_for_every_ticket(
    repo_root: Path,
) -> None:
    """The F1 table flags reopen as a *cascade* gap: ``reopen_ticket`` loops
    ``publish_snapshot`` over ``{ticket_id, *cascaded}``. A regression that
    dropped the cascaded dependents' emits — the silent-update bug H2 exists to
    catch — would slip past a single-ticket assertion, so pin both emits here."""
    from murder.runtime.orchestration.orchestrator import Orchestrator

    rt = _runtime(repo_root)
    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    # parent done; child ready and depending on parent -> cascades to planned.
    _insert_ticket(rt.db, "t201", status="done")
    _insert_ticket(rt.db, "t202", status="ready")
    rt.db.execute(
        "INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES ('t202', 't201')"
    )
    rt.db.commit()

    orch = Orchestrator(rt)
    cascaded = await orch.reopen_ticket("t201")
    await _drain(rt)

    assert "t202" in cascaded
    assert len(_ticket_snapshots(captured, "t201")) == 1, "reopened ticket must emit"
    assert len(_ticket_snapshots(captured, "t202")) == 1, "cascaded dependent must emit"


# === documented-behavior site: startup recovery (no bus yet) =================


def test_recovery_force_fail_runs_before_bus_exists_so_cannot_emit(repo_root: Path) -> None:
    """``reconcile_agents_vs_tmux`` transitions a stuck ``in_progress`` ticket to
    ``failed`` at startup. Per runtime.py it runs at :109 — BEFORE ``run_id`` (:117)
    and ``bus`` (:120) are assigned — so there is no bus to publish on, and its
    signature takes only ``conn`` + ``live_sessions`` (no emit seam exists).

    Documented-no-emit: Ink does a fresh snapshot pull on connect and nothing is
    connected before the bus is created, so the force-fail needs no event. We
    assert the *behavior* (the transition happens); a "no event fired" assertion
    would be vacuous (there is no bus)."""
    from murder.app.service import recovery

    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    # A ticket stuck in_progress whose crow session is gone (live_sessions empty).
    _insert_ticket(conn, "t-stuck", status="in_progress")

    report = recovery.reconcile_agents_vs_tmux(conn, set())

    assert "t-stuck" in report.tickets_reset_to_failed
    from murder.work.tickets import lifecycle as _lc

    assert (
        conn.execute("SELECT status FROM tickets WHERE id = 't-stuck'").fetchone()["status"]
        == TicketStatus.FAILED.value
    )
    # Guard the documented invariant: the reconcile fn has no bus parameter to
    # emit through. If a future change adds one, this comment + the runtime
    # ordering (start() :109 < :120) must be revisited.
    import inspect

    params = set(inspect.signature(recovery.reconcile_agents_vs_tmux).parameters)
    assert params == {"conn", "live_sessions"}
    _ = _lc  # imported for documentation context


def test_startup_recovery_marks_planners_dead_even_when_tmux_session_exists(repo_root: Path) -> None:
    """Planner agents are process-lifetime only.

    If the service goes down and later starts back up, old planner tmux sessions
    must not be treated as resumed live planners. They stay alive while the
    service is running, but startup recovery marks persisted planner rows dead
    and asks Runtime.start() to kill the carried-over tmux sessions.
    """
    from murder.app.service import recovery
    from murder.state.persistence.agents import upsert_agent

    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    upsert_agent(
        conn,
        agent_id="planner-alpha",
        role="planner",
        ticket_id=None,
        session="murder_demo_planner_alpha",
        status="idle",
    )
    upsert_agent(
        conn,
        agent_id="planning_handler-alpha",
        role="planning_handler",
        ticket_id=None,
        session="murder_demo_planning_handler_alpha",
        status="running",
    )
    upsert_agent(
        conn,
        agent_id="collaborator",
        role="collaborator",
        ticket_id=None,
        session="murder_demo_collaborator",
        status="idle",
    )

    report = recovery.reconcile_agents_vs_tmux(
        conn,
        {
            "murder_demo_planner_alpha",
            "murder_demo_planning_handler_alpha",
            "murder_demo_collaborator",
        },
    )

    assert report.agents_marked_dead == ["planner-alpha", "planning_handler-alpha"]
    assert report.sessions_to_kill == [
        "murder_demo_planner_alpha",
        "murder_demo_planning_handler_alpha",
    ]
    rows = {
        row["agent_id"]: row["status"]
        for row in conn.execute(
            "SELECT agent_id, status FROM agents ORDER BY agent_id"
        ).fetchall()
    }
    assert rows["planner-alpha"] == "dead"
    assert rows["planning_handler-alpha"] == "dead"
    assert rows["collaborator"] == "idle"


# === documented-behavior site: CLI retry (separate process, no bus) ==========


def test_cli_retry_transitions_db_only_no_bus_path(repo_root: Path, monkeypatch) -> None:
    """``service_cmd.cmd_retry`` runs in a SEPARATE CLI process with its own
    SQLite connection and NO bus client. It transitions ``failed`` → ``planned``
    and clears ``last_error`` directly in the DB.

    Documented residual gap: the CLI cannot emit (no bus), and it writes the DB
    row only (not the ``.md``), so neither the running service's bus nor its
    filesystem→DB watcher observes the change, and there is no service-side poll
    that re-reads ticket status. The change therefore surfaces on the next Ink
    reconnect (fresh snapshot pull) — the same logic that makes recovery safe.
    We assert the actual DB behavior and document the gap rather than bolting a
    bus client onto the CLI (forbidden by the plan)."""
    from murder.app.cli import service_cmd
    from murder.state.persistence.schema import init_db as _init
    from murder.state.storage.paths import db_path

    conn = get_db(db_path(repo_root))
    _init(conn)
    _insert_ticket(conn, "t-cli", status="failed")
    conn.execute("UPDATE tickets SET last_error = 'boom' WHERE id = 't-cli'")
    conn.commit()
    conn.close()

    # cmd_retry resolves the repo via _repo_root() (cwd); point it at our repo.
    monkeypatch.setattr(service_cmd, "_repo_root", lambda: repo_root)
    service_cmd.cmd_retry("t-cli")

    check = get_db(db_path(repo_root))
    row = check.execute(
        "SELECT status, last_error FROM tickets WHERE id = 't-cli'"
    ).fetchone()
    check.close()
    assert row["status"] == TicketStatus.PLANNED.value
    assert row["last_error"] is None
