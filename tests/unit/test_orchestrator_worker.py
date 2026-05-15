from __future__ import annotations

from pathlib import Path

import pytest

from murder.bus.protocol import CommandEvent
from murder.workers.base import WorkerCtx
from murder.workers.orchestrator_worker import OrchestratorCommandWorker


@pytest.mark.asyncio
async def test_kickoff_ready_command() -> None:
    calls: list[str | None] = []

    async def _kickoff_ready(only: str | None) -> list[str]:
        calls.append(only)
        return ["t001"]

    async def _apply_carve_ready(_tid: str, _payload: dict[str, object]) -> dict[str, object]:
        raise AssertionError("should not be called")

    async def _capture_submit(_payload: dict[str, object]) -> dict[str, object]:
        raise AssertionError("should not be called")

    worker = OrchestratorCommandWorker(
        kickoff_ready=_kickoff_ready,
        apply_carve_ready=_apply_carve_ready,
        capture_submit=_capture_submit,
    )
    result = await worker.on_command(
        CommandEvent(
            run_id="r1",
            target_worker="orchestrator",
            kind="scheduler.kickoff_ready",
            payload={},
            correlation_id="corr-1",
            idempotency_key="idem-1",
        ),
        WorkerCtx(repo_root=Path(".")),
    )

    assert calls == [None]
    assert result == {"handled": True, "kicked": ["t001"]}


@pytest.mark.asyncio
async def test_ticket_apply_carve_ready_command() -> None:
    async def _kickoff_ready(_only: str | None) -> list[str]:  # pragma: no cover
        raise AssertionError("should not be called")

    calls: list[dict[str, object]] = []

    async def _apply_carve(ticket_id: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append(dict(payload))
        assert ticket_id == "t099"
        return {"handled": True, "ok": True, "ticket_id": ticket_id}

    async def _capture_submit(_payload: dict[str, object]) -> dict[str, object]:
        raise AssertionError("should not be called")

    worker = OrchestratorCommandWorker(
        kickoff_ready=_kickoff_ready,
        apply_carve_ready=_apply_carve,
        capture_submit=_capture_submit,
    )
    carve = {"id": "t099", "title": "X", "wave": 1}
    result = await worker.on_command(
        CommandEvent(
            run_id="r1",
            target_worker="orchestrator",
            kind="ticket.apply_carve_ready",
            payload={"ticket_id": "t099", "carve": carve},
            correlation_id="corr-1",
            idempotency_key="idem-1",
        ),
        WorkerCtx(repo_root=Path(".")),
    )
    assert result == {"handled": True, "ok": True, "ticket_id": "t099"}
    assert calls == [{"ticket_id": "t099", "carve": carve}]
