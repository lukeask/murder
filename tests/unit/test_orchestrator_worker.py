from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from murder.bus.protocol import CommandEvent
from murder.runtime.workers.base import WorkerCtx
from murder.runtime.workers.orchestrator_worker import OrchestratorCommandWorker


def _command(payload: dict[str, Any]) -> CommandEvent:
    return CommandEvent(
        id=uuid4(),
        run_id="run",
        target_worker="orchestrator",
        kind="ticket.apply_carve_ready",
        payload=payload,
        correlation_id="c",
        idempotency_key="i",
    )


class _StubOrchestrator:
    """Minimal object implementing the ``OrchestratorCommands`` Protocol.

    Records the ``apply_ticket_carve_ready`` calls the carve tests assert on;
    every other method returns the inert ``{"handled": True}`` placeholder.
    """

    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self._calls = calls

    async def apply_ticket_carve_ready(
        self, ticket_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self._calls.append((ticket_id, payload))
        return {"handled": True, "ok": True, "ticket_id": ticket_id}

    async def kickoff_ready(self, _only: str | None) -> list[str]:
        return []

    async def submit_notetaker_capture(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"handled": True}

    async def retry_failed_ticket(self, _ticket_id: str) -> dict[str, Any]:
        return {"handled": True}

    async def set_schedule_at(
        self, _ticket_id: str, _schedule_at: str | None
    ) -> dict[str, Any]:
        return {"handled": True}

    async def update_ticket_metadata(
        self, _ticket_id: str, _payload: dict[str, Any]
    ) -> dict[str, Any]:
        return {"handled": True}

    async def force_ticket_status(self, _ticket_id: str, _status: str) -> dict[str, Any]:
        return {"handled": True}

    async def ensure_note(self, _name: str) -> dict[str, Any]:
        return {"handled": True}

    async def retire_note(self, _name: str) -> dict[str, Any]:
        return {"handled": True}

    async def send_agent_message(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"handled": True}

    async def send_agent_key(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"handled": True}

    async def refresh_agent_transcript(self, _agent_id: str) -> dict[str, Any]:
        return {"handled": True}

    async def interrupt_agent(self, _agent_id: str) -> dict[str, Any]:
        return {"handled": True}

    async def stop_agent(self, _agent_id: str) -> dict[str, Any]:
        return {"handled": True}

    async def rename_rogue_agent(self, _agent_id: str, _name: str) -> dict[str, Any]:
        return {"handled": True}

    async def scaffold_plan(self, _name: str, _body: str) -> dict[str, Any]:
        return {"handled": True}

    async def rename_plan(self, _old: str, _new: str) -> dict[str, Any]:
        return {"handled": True}

    async def deprecate_plan(self, _name: str) -> dict[str, Any]:
        return {"handled": True}

    async def quick_kick_ticket(self, _title: str) -> dict[str, Any]:
        return {"handled": True}

    def quick_create_ticket(self, _title: str) -> dict[str, Any]:
        return {"handled": True}

    async def spawn_rogue_command(self, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"handled": True}

    async def reconfigure_collaborator(self) -> dict[str, Any]:
        return {"handled": True}


def _worker(calls: list[tuple[str, dict[str, Any]]]) -> OrchestratorCommandWorker:
    return OrchestratorCommandWorker(_StubOrchestrator(calls))


@pytest.mark.asyncio
async def test_apply_carve_ready_rejects_yaml_payload() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    worker = _worker(calls)
    ctx = WorkerCtx(repo_root=Path("."))

    with pytest.raises(ValueError, match="requires non-empty 'carve' in payload"):
        await worker.on_command(
            _command({"ticket_id": "t001", "yaml": "id: t001\ntitle: Old path\n"}),
            ctx,
        )

    assert calls == []


@pytest.mark.asyncio
async def test_apply_carve_ready_forwards_structured_carve_payload() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    worker = _worker(calls)
    ctx = WorkerCtx(repo_root=Path("."))
    payload = {"ticket_id": "t001", "carve": {"title": "Structured"}}

    result = await worker.on_command(_command(payload), ctx)

    assert result == {"handled": True, "ok": True, "ticket_id": "t001"}
    assert calls == [("t001", payload)]
