"""Orchestrator worker accepts ``notetaker.capture.submit``."""

from __future__ import annotations

from pathlib import Path

import pytest

from murder.bus.protocol import CommandEvent
from murder.workers.base import WorkerCtx
from murder.workers.orchestrator_worker import OrchestratorCommandWorker


@pytest.mark.asyncio
async def test_notetaker_capture_submit_command() -> None:
    submitted: list[dict[str, object]] = []

    async def _kickoff_ready(_only: str | None) -> list[str]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _apply_carve_ready(_tid: str, _payload: dict[str, object]) -> dict[str, object]:
        raise AssertionError("should not be called")

    async def _capture_submit(payload: dict[str, object]) -> dict[str, object]:
        submitted.append(dict(payload))
        return {
            "entry_id": 7,
            "cleaned": "## X",
            "short_vers": "ok",
            "reply": "ok",
        }

    async def _retry_failed(_ticket_id: str) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _set_schedule_at(_ticket_id: str, _schedule_at: str | None) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    worker = OrchestratorCommandWorker(
        kickoff_ready=_kickoff_ready,
        apply_carve_ready=_apply_carve_ready,
        capture_submit=_capture_submit,
        retry_failed=_retry_failed,
        set_schedule_at=_set_schedule_at,
    )
    result = await worker.on_command(
        CommandEvent(
            run_id="r1",
            target_worker="orchestrator",
            kind="notetaker.capture.submit",
            payload={"text": "hello"},
            correlation_id="corr-1",
            idempotency_key="idem-1",
        ),
        WorkerCtx(repo_root=Path(".")),
    )

    assert submitted == [{"text": "hello"}]
    assert result == {
        "handled": True,
        "entry_id": 7,
        "cleaned": "## X",
        "short_vers": "ok",
        "reply": "ok",
    }
