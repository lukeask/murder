from __future__ import annotations

from pathlib import Path

import pytest

from murder.bus.protocol import CommandEvent, Entity, StateSnapshotEvent
from murder.db import insert_run
from murder.workers.base import WorkerCtx
from murder.workers.state_worker import StateCommandWorker

EXPECTED_SEVERITY = 2


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_state_worker_creates_escalation_and_snapshot(memdb) -> None:
    insert_run(memdb, "r1", "{}")
    bus = _RecordingBus()
    worker = StateCommandWorker()

    result = await worker.on_command(
        CommandEvent(
            run_id="r1",
            target_worker="state",
            kind="state.escalation.create",
            payload={
                "reason": "needs human",
                "severity": 2,
                "to_recipient": "user",
            },
            correlation_id="corr-1",
            idempotency_key="idem-1",
        ),
        WorkerCtx(repo_root=Path("."), db=memdb, bus=bus, run_id="r1"),
    )

    assert result == {"handled": True, "escalation_id": 1}
    row = memdb.execute("SELECT reason, severity FROM escalations WHERE id = 1").fetchone()
    assert row["reason"] == "needs human"
    assert row["severity"] == EXPECTED_SEVERITY
    assert len(bus.events) == 1
    snapshot = bus.events[0]
    assert isinstance(snapshot, StateSnapshotEvent)
    assert snapshot.entity == Entity.ESCALATION
    assert snapshot.key == "1"


@pytest.mark.asyncio
async def test_state_worker_validates_escalation_payload(memdb) -> None:
    worker = StateCommandWorker()

    with pytest.raises(ValueError, match="payload.reason"):
        await worker.on_command(
            CommandEvent(
                run_id="r1",
                target_worker="state",
                kind="state.escalation.create",
                payload={"reason": ""},
                correlation_id="corr-1",
                idempotency_key="idem-1",
            ),
            WorkerCtx(repo_root=Path("."), db=memdb),
        )
