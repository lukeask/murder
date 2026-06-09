"""F1 — key-only event uniformity: QUEUE_ROW entity emit sites.

Sibling of ``test_f1_keyonly_snapshot.py`` (AGENT backbone),
``test_f1_keyonly_snapshot_ticket.py`` (TICKET), ``..._plan.py`` (PLAN), and
``..._note.py`` (NOTE). Proves that usage/scheduler read-model mutations — the
state behind the usage gauges embedded in ``state.schedule_snapshot`` (plan
lines 322-323: there is no ``queue_row`` table; ``Entity.QUEUE_ROW`` is the
invalidation key for those gauges) — funnel a single key-only
``state.snapshot{entity=queue_row, key=...}`` through the established worker
choke points.

These are *workers* (not Runtime), so they ``await ctx.bus.publish(
StateSnapshotEvent(...))`` directly — the backbone's "async callers await
bus.publish directly" rule. There is no ``Runtime.emit_snapshot`` here.

Two choke points in scope:
- ``SchedulerWorker._evaluate_window`` (beside the existing
  ``SchedulerDecisionEvent``) — fires when ``scheduler_decision_cache`` changes;
  key = ``f"{harness}:{window_key}"`` (the cache primary key).
- ``UsageProbeWorker.on_command`` — fires when the sampler inserts into
  ``harness_usage_snapshots``; key = harness (one per sampled kind).

NOT in scope (ticket entity, the mode chunk owns it): ``scheduler.set_mode``
flips ``scheduler_state.mode`` and must emit ``ticket``, never ``queue_row``.
A negative test asserts set_mode emits no ``queue_row``.

CROSS-PROCESS CAVEAT: in production ``UsageProbeWorker`` runs in a subprocess
whose ``WorkerCtx`` gets a DB-backed ``Bus`` (wired in ``process_targets.py``);
``Bus.publish`` persists to the shared ``events`` table before fan-out and the
client tails it (``DurableBroker.tail``), so the emit crosses the process
boundary. These in-process unit tests construct the worker directly and so do
NOT exercise that subprocess wiring — they prove the emit *fires* with a live
bus, not that cross-process delivery is unbroken.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from murder.bus import Bus
from murder.bus.protocol import (
    Entity,
    SchedulerDecisionEvent,
    StateSnapshotEvent,
)
from murder.runtime.scheduler.worker import SchedulerWorker
from murder.runtime.workers.base import WorkerCtx
from murder.runtime.workers.usage_probe_worker import UsageProbeWorker
from murder.state.persistence.schema import get_db, init_db
from murder.state.persistence.usage_status import UsageWindow


def _ctx(repo_root: Path) -> WorkerCtx:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    bus = Bus("run-test", conn)
    return WorkerCtx(repo_root=repo_root, db=conn, bus=bus, run_id="run-test")


async def _record(sink: list[object], ev: object) -> None:
    sink.append(ev)


def _queue_row_snapshots(
    captured: list[object], key: str | None = None
) -> list[StateSnapshotEvent]:
    return [
        e
        for e in captured
        if isinstance(e, StateSnapshotEvent)
        and e.entity == Entity.QUEUE_ROW
        and (key is None or e.key == key)
    ]


# === scheduler decision-cache choke point ====================================


@pytest.mark.asyncio
async def test_evaluate_window_emits_one_key_only_queue_row_snapshot(
    repo_root: Path,
) -> None:
    ctx = _ctx(repo_root)
    captured: list[object] = []
    ctx.bus.subscribe(lambda ev: _record(captured, ev))

    now = datetime.now(timezone.utc)
    window = UsageWindow(
        name="5h",
        percent_used=42.0,
        reset_at=(now + timedelta(hours=3)).isoformat(),
        starts_at=now.isoformat(),
        ends_at=(now + timedelta(hours=5)).isoformat(),
    )

    worker = SchedulerWorker()
    await worker._evaluate_window(ctx, "codex", window, now)

    snaps = _queue_row_snapshots(captured, "codex:5h")
    assert len(snaps) == 1
    assert snaps[0].payload is None  # key-only by contract

    # The typed event is preserved (not replaced) -- contract is additive.
    decisions = [e for e in captured if isinstance(e, SchedulerDecisionEvent)]
    assert len(decisions) == 1


# === usage-probe insert choke point ==========================================


@pytest.mark.asyncio
async def test_usage_probe_emits_queue_row_per_sampled_harness(repo_root: Path) -> None:
    ctx = _ctx(repo_root)
    captured: list[object] = []
    ctx.bus.subscribe(lambda ev: _record(captured, ev))

    async def _sample(_ctx: WorkerCtx) -> tuple[int, int]:
        return (2, 0)  # two snapshots stored, no failures

    worker = UsageProbeWorker(
        sampler=_sample,
        kinds_provider=lambda _ctx: ["codex", "claude"],
    )
    from murder.bus.protocol import CommandEvent

    cmd = CommandEvent(
        run_id="run-test",
        agent_id="tester",
        target_worker="usage-probe",
        kind="state.harness_usage.sample",
        payload={},
        correlation_id="c1",
        idempotency_key="k1",
    )
    result = await worker.on_command(cmd, ctx)
    assert result["handled"] is True

    codex = _queue_row_snapshots(captured, "codex")
    claude = _queue_row_snapshots(captured, "claude")
    assert len(codex) == 1
    assert len(claude) == 1
    assert all(e.payload is None for e in (*codex, *claude))


@pytest.mark.asyncio
async def test_usage_probe_does_not_emit_when_nothing_stored(repo_root: Path) -> None:
    ctx = _ctx(repo_root)
    captured: list[object] = []
    ctx.bus.subscribe(lambda ev: _record(captured, ev))

    async def _sample(_ctx: WorkerCtx) -> tuple[int, int]:
        return (0, 1)  # all sampling failed -> no snapshot row inserted

    worker = UsageProbeWorker(
        sampler=_sample,
        kinds_provider=lambda _ctx: ["codex"],
    )
    from murder.bus.protocol import CommandEvent

    cmd = CommandEvent(
        run_id="run-test",
        agent_id="tester",
        target_worker="usage-probe",
        kind="state.harness_usage.sample",
        payload={},
        correlation_id="c2",
        idempotency_key="k2",
    )
    await worker.on_command(cmd, ctx)

    assert _queue_row_snapshots(captured) == []


# === negative: mode change is TICKET, not QUEUE_ROW ==========================


@pytest.mark.asyncio
async def test_set_mode_does_not_emit_queue_row(repo_root: Path) -> None:
    ctx = _ctx(repo_root)
    # Seed scheduler_state so set_mode has a from_mode row.
    now = datetime.now(timezone.utc).isoformat()
    ctx.db.execute(
        "INSERT OR IGNORE INTO scheduler_state(id, mode, updated_at) VALUES (1, 'manual', ?)",
        (now,),
    )
    captured: list[object] = []
    ctx.bus.subscribe(lambda ev: _record(captured, ev))

    from murder.bus.protocol import CommandEvent

    cmd = CommandEvent(
        run_id="run-test",
        agent_id="tester",
        target_worker="scheduler",
        kind=SchedulerWorker.SET_MODE,
        payload={"mode": "autorun_ready"},
        correlation_id="c3",
        idempotency_key="k3",
    )
    worker = SchedulerWorker()
    await worker._handle_set_mode(cmd, ctx)

    # Mode changes are the mode-chunk's TICKET responsibility, not queue_row.
    assert _queue_row_snapshots(captured) == []
