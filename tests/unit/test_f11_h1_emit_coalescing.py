"""F11 H1 — high-rate emit coalescing policy.

F1 emits a key-only ``state.snapshot{entity}`` on every read-model mutation, but
three sites fire at storm rates and would make the Ink TUI refetch in a tight
loop. H1 adopts an emit-on-visible-change / debounce policy at each. This module
is the ceiling proof: under a synthetic high-rate load the per-window count of
``agent`` / ``queue_row`` / ``plan`` invalidations is BOUNDED, while no live panel
stops updating as a result of the suppression.

The three sites and their gate:

1. **Heartbeat** (``CrowHandler._orchestration_tick`` -> ``heartbeat_agent``): the
   plain beat only bumps ``last_heartbeat_at``; the sole consumer is the Ink
   client-side "stuck" flag. Emit ``agent`` only on a ``HEARTBEAT_EMIT_BUCKET_S``
   bucket crossing (half ``STUCK_AFTER`` = 30s), bounded to <=1/bucket/agent — but
   never zero, so a healthy crow's ``last_seen`` stays fresh and never flips to
   false-stuck (the roster is event-driven, with no client refetch timer).

2. **Scheduler decision** (``SchedulerWorker._evaluate_window``): emit
   ``queue_row`` only when a field ``state.schedule_snapshot`` actually renders from
   ``scheduler_decision_cache`` (``decision`` / ``rationale`` / ``kicked_ticket_id``)
   changes — NOT every 10s tick's continuous usage/threshold drift.

3. **Conversation rebuild / plan re-sort** (``Daemon._project_transcript``): the
   content path (``conversation.block``) is already bounded by producer hash-skip;
   the only key-only candidate is the ``plan`` list re-sort, emitted ONLY for a
   ``planner-*`` agent AND ONLY when the poll produced real block changes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from murder.bus import Bus
from murder.bus.protocol import Entity, StateSnapshotEvent
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.runtime.agents.base import AgentRole, AgentStatus, HarnessBackedAgent
from murder.runtime.scheduler.worker import SchedulerWorker
from murder.runtime.workers.base import WorkerCtx
from murder.state.persistence.agents import (
    HEARTBEAT_EMIT_BUCKET_S,
    heartbeat_bucket,
)
from murder.state.persistence.runs import insert_run
from murder.state.persistence.schema import get_db, init_db
from murder.state.persistence.usage_status import UsageWindow


def _ctx(repo_root: Path) -> WorkerCtx:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    insert_run(conn, "run-test", "{}")
    bus = Bus("run-test", conn)
    return WorkerCtx(repo_root=repo_root, db=conn, bus=bus, run_id="run-test")


async def _record(sink: list[object], ev: object) -> None:
    sink.append(ev)


def _snaps(captured: list[object], entity: Entity) -> list[StateSnapshotEvent]:
    return [
        e for e in captured if isinstance(e, StateSnapshotEvent) and e.entity == entity
    ]


# === site 1: heartbeat bucket gate ===========================================


def test_heartbeat_bucket_is_pure_monotonic_arithmetic() -> None:
    """The gate is a pure ``floor(now / bucket)`` on an injected clock — no wall
    clock, no sleep — so it is deterministic under the conftest noop-sleep patch."""
    b = HEARTBEAT_EMIT_BUCKET_S
    assert heartbeat_bucket(0.0) == 0
    assert heartbeat_bucket(b - 0.001) == 0  # still inside bucket 0
    assert heartbeat_bucket(b) == 1  # crossing advances exactly once
    assert heartbeat_bucket(2 * b + 5) == 2


def test_heartbeat_emits_are_bounded_by_bucket_under_5hz_load() -> None:
    """A steady 5Hz heartbeat over a window emits ``agent`` at most ceil(window /
    bucket) times — the storm is coalesced — yet emits at least once per bucket so
    ``last_seen`` never freezes (no false-stuck)."""
    bucket = HEARTBEAT_EMIT_BUCKET_S
    window_s = 5 * bucket  # 5 buckets of wall time
    beat_interval = 0.2  # 5Hz

    emitted_buckets: list[int] = []
    last_emitted: int | None = None  # mirrors CrowHandler._last_heartbeat_emit_bucket
    now = 0.0
    while now <= window_s:
        b = heartbeat_bucket(now)
        if b != last_emitted:  # the production gate
            last_emitted = b
            emitted_buckets.append(b)
        now += beat_interval

    beats = int(window_s / beat_interval) + 1  # ~126 beats at 5Hz over 150s
    assert beats >= 100  # sanity: we really did simulate a storm
    # Ceiling: one emit per distinct bucket, NOT one per beat.
    assert len(emitted_buckets) <= (window_s / bucket) + 1
    assert len(emitted_buckets) < beats  # strictly coalesced
    # Liveness floor: every bucket that elapsed got an emit, so last_seen stays
    # fresh within one bucket (< STUCK_AFTER) — a live crow never reads as stuck.
    assert emitted_buckets == sorted(set(emitted_buckets))
    assert len(emitted_buckets) >= 5


@pytest.mark.asyncio
async def test_crow_handler_heartbeat_emit_bounded_over_window(
    repo_root: Path, fake_tmux, monkeypatch
) -> None:
    """Drives the REAL ``CrowHandler._orchestration_tick`` (the heartbeat call site)
    repeatedly across a simulated window with a stepped ``time.monotonic``: a benign
    pane every tick still bumps ``last_heartbeat_at`` in the DB, but the key-only
    ``agent`` invalidation is coalesced to <=1 per HEARTBEAT_EMIT_BUCKET_S — bounded,
    never per-tick, yet at least once per elapsed bucket (no false-stuck)."""
    from murder.config import CrowHandlerConfig
    from murder.runtime.agents.crow_handler import CrowHandler
    from murder.runtime.orchestration.outcome import TicketOutcomeService
    from murder.bus import Bus as _Bus

    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    conn.execute(
        "INSERT INTO runs(run_id, started_at, config_snapshot)"
        " VALUES ('run-test', '2026-01-01', '{}')"
    )
    conn.execute(
        "INSERT INTO tickets(id, title, status, created_at, updated_at)"
        " VALUES ('t001', 'T', 'in_progress', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO agents(agent_id, role, ticket_id, status, started_at)"
        " VALUES ('crow_handler-t001', 'crow_handler', 't001', 'running', '2026-01-01')"
    )

    runtime = MagicMock()
    runtime.db = conn
    runtime.bus = _Bus("run-test", conn)
    runtime.run_id = "run-test"
    runtime.sync_agent = MagicMock()
    agent_emit_keys: list[str] = []

    async def _publish_snapshot(entity: Entity, key: str) -> None:
        if entity == Entity.AGENT:
            agent_emit_keys.append(key)

    runtime.publish_snapshot = _publish_snapshot

    handler = CrowHandler(
        agent_id="crow_handler-t001",
        ticket_id="t001",
        session="handler-log",
        crow_session="crow-t001",
        harness=ClaudeCodeAdapter(),
        config=CrowHandlerConfig(model="test", poll_interval_s=0.0),
        repo_root=repo_root,
        runtime=runtime,
        outcome=MagicMock(spec=TicketOutcomeService),
        coordinator=MagicMock(),
    )

    bucket = HEARTBEAT_EMIT_BUCKET_S
    # 100 orchestration ticks spread over 4 buckets of simulated monotonic time.
    window_s = 4 * bucket
    ticks = 100
    clock = {"t": 0.0}
    step = window_s / ticks
    monkeypatch.setattr(
        "murder.runtime.agents.crow_handler.time.monotonic", lambda: clock["t"]
    )
    for _ in range(ticks):
        await handler._orchestration_tick("just working, nothing to see\n")
        clock["t"] += step

    # Ceiling: bounded to <= one emit per elapsed bucket, NOT one per tick.
    assert len(agent_emit_keys) <= 5  # ceil(window / bucket) + boundary
    assert len(agent_emit_keys) < ticks
    assert set(agent_emit_keys) == {"crow_handler-t001"}
    # Liveness: at least one emit per bucket crossed, so last_seen never freezes.
    assert len(agent_emit_keys) >= 4
    # The DB heartbeat itself still landed every tick (last_heartbeat_at is fresh).
    row = conn.execute(
        "SELECT last_heartbeat_at FROM agents WHERE agent_id = 'crow_handler-t001'"
    ).fetchone()
    assert row["last_heartbeat_at"] is not None


# === site 2: scheduler decision visible-change gate ==========================


def _window(now: datetime, pct: float) -> UsageWindow:
    return UsageWindow(
        name="5h",
        percent_used=pct,
        reset_at=(now + timedelta(hours=3)).isoformat(),
        starts_at=now.isoformat(),
        ends_at=(now + timedelta(hours=5)).isoformat(),
    )


@pytest.mark.asyncio
async def test_scheduler_coalesces_queue_row_across_unchanged_decision_ticks(
    repo_root: Path,
) -> None:
    """Many 10s ticks where only the continuous usage drifts WITHIN a rendered
    percent (rationale text unchanged, decision unchanged) collapse to a single
    ``queue_row`` invalidation — not one per tick."""
    ctx = _ctx(repo_root)
    captured: list[object] = []
    ctx.bus.subscribe(lambda ev: _record(captured, ev))
    worker = SchedulerWorker()
    now = datetime.now(timezone.utc)

    # 20 ticks; usage drifts 42.00 -> 42.19, all rounding to "42%" -> same
    # rationale, same hold decision, no kicked ticket.
    for i in range(20):
        await worker._evaluate_window(ctx, "codex", _window(now, 42.0 + i * 0.01), now)

    snaps = _snaps(captured, Entity.QUEUE_ROW)
    assert len(snaps) == 1, f"expected coalesced to 1, got {len(snaps)}"
    assert snaps[0].payload is None  # key-only by contract


@pytest.mark.asyncio
async def test_scheduler_emits_queue_row_on_rendered_field_change(
    repo_root: Path,
) -> None:
    """A change to a RENDERED decision-cache field (here: the whole-percent in the
    rationale, 42% -> 50%) DOES re-emit, so the panel is never stuck stale."""
    ctx = _ctx(repo_root)
    captured: list[object] = []
    ctx.bus.subscribe(lambda ev: _record(captured, ev))
    worker = SchedulerWorker()
    now = datetime.now(timezone.utc)

    await worker._evaluate_window(ctx, "codex", _window(now, 42.0), now)  # emit #1
    await worker._evaluate_window(ctx, "codex", _window(now, 42.1), now)  # coalesced
    await worker._evaluate_window(ctx, "codex", _window(now, 50.0), now)  # emit #2

    snaps = _snaps(captured, Entity.QUEUE_ROW)
    assert len(snaps) == 2


# === site 3: plan re-sort gate (planner + real changes only) =================


def _fake_change():
    """A minimal real ``ConversationBlockChange`` so the producer's per-change
    publish loop runs end-to-end (its ``.action`` / ``.block`` are accessed)."""
    from murder.state.persistence.conversation import (
        ConversationBlock,
        ConversationBlockChange,
    )

    block = ConversationBlock(
        id=1,
        conversation_id="planner-alpha",
        ordinal=0,
        kind="assistant_final",
        payload={"type": "assistant", "phase": "final", "text": "hi"},
        sealed=True,
        service_received_at="2026-06-09T00:00:00",
    )
    return ConversationBlockChange(action="block-appended", block=block)


class _StubAgent(HarnessBackedAgent):
    """Minimal concrete ``HarnessBackedAgent`` driving the REAL projection HOT path
    (``project_once`` -> ``ConversationProducer.poll``), where the F11 H1 plan
    re-sort gate lives. Abstract lifecycle methods are unused here."""

    role = AgentRole.PLANNER

    def __init__(self, agent_id: str, runtime: object) -> None:
        self.id = agent_id
        self.session = "stub-session"
        self.ticket_id = None
        self.status = AgentStatus.RUNNING
        self.runtime = runtime
        self.harness = ClaudeCodeAdapter()
        self._producer = None
        self._accumulator = None
        self._build_producer()  # real ConversationProducer + its hash-skip

    async def start(self, brief: str, ctx: dict) -> None: ...  # pragma: no cover
    async def stop(self, *, failed: bool = False, kill_session: bool = True) -> None: ...  # pragma: no cover
    async def send(self, msg: str):  # pragma: no cover
        return None


def _planner_runtime(conn) -> tuple[object, list[tuple[Entity, str]]]:
    runtime = MagicMock()
    runtime.db = conn
    runtime.bus = MagicMock()
    runtime.bus.publish = AsyncMock()  # producer awaits conversation.block publishes
    runtime.run_id = "run-test"
    calls: list[tuple[Entity, str]] = []

    async def _publish_snapshot(entity: Entity, key: str) -> None:
        calls.append((entity, key))

    runtime.publish_snapshot = _publish_snapshot
    return runtime, calls


@pytest.mark.asyncio
async def test_project_once_emits_plan_only_for_planner_with_changes(
    repo_root: Path, fake_tmux, monkeypatch
) -> None:
    """Drives the REAL ``project_once`` hot path (service projection ticker ->
    ``ConversationProducer.poll``). The producer reconcile rewrites ``agent_messages``
    (the plans re-sort trigger), and the H1 gate emits ``plan`` ONLY for a planner
    AND ONLY when the poll produced real changes; a non-planner agent on the same
    hot path emits nothing.
    """
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)

    # Control the producer's reconcile output (its only mutating dependency) so the
    # test is not coupled to harness-parser internals; everything else — the hash-skip,
    # the project_once capture, the gate — runs through production code.
    monkeypatch.setattr(
        "murder.state.persistence.conversation.project_parsed_doc_with_changes",
        lambda _c, _a, _d: ({"segments": []}, [_fake_change()]),
    )

    runtime, calls = _planner_runtime(conn)
    planner = _StubAgent("planner-alpha", runtime)
    fake_tmux.queue_pane("assistant: hello\n")
    await planner.project_once()
    assert calls == [(Entity.PLAN, "alpha")]

    # A non-planner crow on the SAME hot path WITH changes -> no plan emit
    # (its content rides conversation.block; only the plans list re-sorts on planners).
    runtime2, calls2 = _planner_runtime(conn)
    crow = _StubAgent("crow_handler-t001", runtime2)
    fake_tmux.queue_pane("assistant: hello\n")
    await crow.project_once()
    assert calls2 == []


@pytest.mark.asyncio
async def test_project_once_coalesces_plan_emit_on_hash_skip(
    repo_root: Path, fake_tmux, monkeypatch
) -> None:
    """A chatty planner polled repeatedly by the ticker emits ``plan`` only on poll
    ticks that actually changed the pane: the producer's hash-skip drops unchanged
    re-polls, so N identical polls collapse to ONE plan invalidation — the ceiling."""
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)

    monkeypatch.setattr(
        "murder.state.persistence.conversation.project_parsed_doc_with_changes",
        lambda _c, _a, _d: ({"segments": []}, [_fake_change()]),
    )

    runtime, calls = _planner_runtime(conn)
    planner = _StubAgent("planner-alpha", runtime)

    # 10 ticker polls of the SAME pane content -> producer hash-skips 9 of them.
    for _ in range(10):
        fake_tmux.queue_pane("assistant: same pane\n")
        await planner.project_once()

    assert len(calls) == 1, f"expected hash-skip to coalesce to 1, got {len(calls)}"
    assert calls[0] == (Entity.PLAN, "alpha")
