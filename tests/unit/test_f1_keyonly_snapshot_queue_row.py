"""F1 — key-only event uniformity: QUEUE_ROW entity emit sites.

Sibling of ``test_f1_keyonly_snapshot.py`` (AGENT backbone),
``test_f1_keyonly_snapshot_ticket.py`` (TICKET), ``..._plan.py`` (PLAN), and
``..._note.py`` (NOTE). Proves that usage/scheduler read-model mutations — the
state behind the usage gauges embedded in ``state.schedule_snapshot`` (plan
lines 322-323: there is no ``queue_row`` table; ``Entity.QUEUE_ROW`` is the
invalidation key for those gauges) — funnel a single key-only
``state.snapshot{entity=queue_row, key=...}`` through the established worker
choke points.

These are workers (not Runtime), and they write the schedule projection inputs
directly. There is no event-bus snapshot path.

Two choke points in scope:
- ``SchedulerWorker._evaluate_window`` (beside the existing
  ``SchedulerDecisionEvent``) — fires when ``scheduler_decision_cache`` changes;
  key = ``f"{harness}:{window_key}"`` (the cache primary key).
- ``UsageProbeWorker.on_command`` — fires when the sampler inserts into
  ``harness_usage_snapshots``; key = harness (one per sampled kind).

NOT in scope (ticket entity, the mode chunk owns it): ``scheduler.set_mode``
flips ``scheduler_state.mode`` and must emit ``ticket``, never ``queue_row``.
A negative test asserts set_mode emits no ``queue_row``.

These in-process tests construct the workers directly and assert the durable
projection inputs they write; there is no cross-process event replay involved.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from murder.runtime.orchestration.notifier import OrchestrationNotifier
from murder.runtime.scheduler.worker import SchedulerWorker
from murder.runtime.workers.base import WorkerCtx
from murder.runtime.workers.usage_probe_worker import UsageProbeWorker
from murder.state.persistence.runs import insert_run
from murder.state.persistence.schema import get_db, init_db
from murder.state.persistence.usage_status import UsageWindow


def _ctx(repo_root: Path) -> WorkerCtx:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    insert_run(conn, "run-test", "{}")
    bus = OrchestrationNotifier(conn)
    return WorkerCtx(repo_root=repo_root, db=conn, bus=bus, run_id="run-test")


# === scheduler decision-cache choke point ====================================


@pytest.mark.asyncio
async def test_evaluate_window_writes_one_queue_row_projection_input(
    repo_root: Path,
) -> None:
    ctx = _ctx(repo_root)

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

    input_row = ctx.db.execute(
        "SELECT projection, subject_key, generation FROM projection_inputs "
        "WHERE projection = 'schedule' AND subject_key = 'decision:codex:5h'"
    ).fetchone()
    assert input_row is not None
    assert input_row["generation"] == 0

# === usage-probe insert choke point ==========================================


@pytest.mark.asyncio
async def test_usage_probe_writes_queue_row_per_sampled_harness(repo_root: Path) -> None:
    ctx = _ctx(repo_root)

    async def _sample(
        _ctx: WorkerCtx,
        *,
        modes: set[str] | None = None,
    ) -> tuple[int, int]:
        del modes
        return (2, 0)  # two snapshots stored, no failures

    worker = UsageProbeWorker(
        sampler=_sample,
        kinds_provider=lambda _ctx, modes=None: ["codex", "claude"],
    )
    from murder.runtime.orchestration.events import CommandEvent
    from murder.runtime.orchestration.commands import OrchestrationCommand
    from murder.runtime.orchestration.worker_names import WorkerName

    cmd = CommandEvent(
        run_id="run-test",
        agent_id="tester",
        target_worker=WorkerName.USAGE_PROBE,
        kind=OrchestrationCommand.STATE_HARNESS_USAGE_SAMPLE,
        payload={},
        correlation_id="c1",
        idempotency_key="k1",
    )
    result = await worker.on_command(cmd, ctx)
    assert result["handled"] is True

    inputs = ctx.db.execute(
        "SELECT subject_key FROM projection_inputs WHERE projection = 'schedule' ORDER BY sequence"
    ).fetchall()
    assert [row["subject_key"] for row in inputs] == ["usage:codex", "usage:claude"]


@pytest.mark.asyncio
async def test_usage_probe_does_not_write_when_nothing_stored(repo_root: Path) -> None:
    ctx = _ctx(repo_root)

    async def _sample(
        _ctx: WorkerCtx,
        *,
        modes: set[str] | None = None,
    ) -> tuple[int, int]:
        del modes
        return (0, 1)  # all sampling failed -> no snapshot row inserted

    worker = UsageProbeWorker(
        sampler=_sample,
        kinds_provider=lambda _ctx, modes=None: ["codex"],
    )
    from murder.runtime.orchestration.events import CommandEvent
    from murder.runtime.orchestration.commands import OrchestrationCommand
    from murder.runtime.orchestration.worker_names import WorkerName

    cmd = CommandEvent(
        run_id="run-test",
        agent_id="tester",
        target_worker=WorkerName.USAGE_PROBE,
        kind=OrchestrationCommand.STATE_HARNESS_USAGE_SAMPLE,
        payload={},
        correlation_id="c2",
        idempotency_key="k2",
    )
    await worker.on_command(cmd, ctx)

    assert ctx.db.execute(
        "SELECT 1 FROM projection_inputs WHERE projection = 'schedule'"
    ).fetchone() is None


# === negative: mode change is TICKET, not QUEUE_ROW ==========================


@pytest.mark.asyncio
async def test_set_mode_does_not_write_queue_row(repo_root: Path) -> None:
    ctx = _ctx(repo_root)
    # Seed scheduler_state so set_mode has a from_mode row.
    now = datetime.now(timezone.utc).isoformat()
    ctx.db.execute(
        "INSERT OR IGNORE INTO scheduler_state(id, mode, updated_at) VALUES (1, 'manual', ?)",
        (now,),
    )

    from murder.runtime.orchestration.events import CommandEvent
    from murder.runtime.orchestration.worker_names import WorkerName

    cmd = CommandEvent(
        run_id="run-test",
        agent_id="tester",
        target_worker=WorkerName.SCHEDULER,
        kind=SchedulerWorker.SET_MODE,
        payload={"mode": "autorun_ready"},
        correlation_id="c3",
        idempotency_key="k3",
    )
    worker = SchedulerWorker()
    await worker._handle_set_mode(cmd, ctx)

    rows = ctx.db.execute(
        "SELECT subject_key FROM projection_inputs WHERE projection = 'schedule'"
    ).fetchall()
    assert rows == []
