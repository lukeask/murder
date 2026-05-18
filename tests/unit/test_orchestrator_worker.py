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

    async def _retry_failed(_ticket_id: str) -> dict[str, object]:
        raise AssertionError("should not be called")

    async def _set_schedule_at(_ticket_id: str, _schedule_at: str | None) -> dict[str, object]:
        raise AssertionError("should not be called")

    async def _update_metadata(_t: str, _p: dict[str, object]) -> dict[str, object]:
        raise AssertionError("should not be called")

    async def _force_status(_t: str, _s: str) -> dict[str, object]:
        raise AssertionError("should not be called")

    worker = OrchestratorCommandWorker(
        kickoff_ready=_kickoff_ready,
        apply_carve_ready=_apply_carve_ready,
        capture_submit=_capture_submit,
        retry_failed=_retry_failed,
        set_schedule_at=_set_schedule_at,
        update_metadata=_update_metadata,
        force_status=_force_status,
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

    async def _capture_submit(_payload: dict[str, object]) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _retry_failed(_ticket_id: str) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _set_schedule_at(_ticket_id: str, _schedule_at: str | None) -> dict[str, object]:
        raise AssertionError("should not be called")

    async def _update_metadata(
        _t: str, _p: dict[str, object]
    ) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _force_status(_t: str, _s: str) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    worker = OrchestratorCommandWorker(
        kickoff_ready=_kickoff_ready,
        apply_carve_ready=_apply_carve,
        capture_submit=_capture_submit,
        retry_failed=_retry_failed,
        set_schedule_at=_set_schedule_at,
        update_metadata=_update_metadata,
        force_status=_force_status,
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


@pytest.mark.asyncio
async def test_ticket_retry_failed_command() -> None:
    retried: list[str] = []

    async def _kickoff_ready(_only: str | None) -> list[str]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _apply_carve_ready(
        _tid: str, _payload: dict[str, object]
    ) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _capture_submit(_payload: dict[str, object]) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _retry_failed(ticket_id: str) -> dict[str, object]:
        retried.append(ticket_id)
        return {"handled": True, "ticket_id": ticket_id, "prev_status": "failed"}

    async def _set_schedule_at(_ticket_id: str, _schedule_at: str | None) -> dict[str, object]:
        raise AssertionError("should not be called")

    async def _update_metadata(
        _t: str, _p: dict[str, object]
    ) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _force_status(_t: str, _s: str) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    worker = OrchestratorCommandWorker(
        kickoff_ready=_kickoff_ready,
        apply_carve_ready=_apply_carve_ready,
        capture_submit=_capture_submit,
        retry_failed=_retry_failed,
        set_schedule_at=_set_schedule_at,
        update_metadata=_update_metadata,
        force_status=_force_status,
    )
    result = await worker.on_command(
        CommandEvent(
            run_id="r1",
            target_worker="orchestrator",
            kind="ticket.retry_failed",
            payload={"ticket_id": "t042"},
            correlation_id="corr-3",
            idempotency_key="idem-3",
        ),
        WorkerCtx(repo_root=Path(".")),
    )
    assert retried == ["t042"]
    assert result == {"handled": True, "ticket_id": "t042", "prev_status": "failed"}


@pytest.mark.asyncio
async def test_ticket_retry_failed_missing_ticket_id_raises() -> None:
    async def _noop(_ticket_id: str) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    worker = OrchestratorCommandWorker(
        kickoff_ready=lambda _: (_ for _ in ()).throw(AssertionError),  # type: ignore[arg-type]
        apply_carve_ready=lambda _t, _p: (_ for _ in ()).throw(AssertionError),  # type: ignore[arg-type]
        capture_submit=lambda _p: (_ for _ in ()).throw(AssertionError),  # type: ignore[arg-type]
        retry_failed=_noop,
        set_schedule_at=lambda _t, _s: (_ for _ in ()).throw(AssertionError),  # type: ignore[arg-type]
        update_metadata=lambda _t, _p: (_ for _ in ()).throw(AssertionError),  # type: ignore[arg-type]
        force_status=lambda _t, _s: (_ for _ in ()).throw(AssertionError),  # type: ignore[arg-type]
    )
    with pytest.raises(ValueError, match="ticket_id"):
        await worker.on_command(
            CommandEvent(
                run_id="r1",
                target_worker="orchestrator",
                kind="ticket.retry_failed",
                payload={},
                correlation_id="corr-4",
                idempotency_key="idem-4",
            ),
            WorkerCtx(repo_root=Path(".")),
        )
