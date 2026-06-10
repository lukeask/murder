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


def _worker(calls: list[tuple[str, dict[str, Any]]]) -> OrchestratorCommandWorker:
    async def apply_carve_ready(ticket_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append((ticket_id, payload))
        return {"handled": True, "ok": True, "ticket_id": ticket_id}

    async def kickoff_ready(_only: str | None) -> list[str]:
        return []

    async def dict_result(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"handled": True}

    async def agent_message(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"handled": True}

    async def agent_key(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"handled": True}

    async def note_name(_name: str) -> dict[str, Any]:
        return {"handled": True}

    async def ticket_str(_ticket_id: str) -> dict[str, Any]:
        return {"handled": True}

    async def ticket_schedule(_ticket_id: str, _schedule_at: str | None) -> dict[str, Any]:
        return {"handled": True}

    async def ticket_metadata(_ticket_id: str, _payload: dict[str, Any]) -> dict[str, Any]:
        return {"handled": True}

    async def ticket_status(_ticket_id: str, _status: str) -> dict[str, Any]:
        return {"handled": True}

    async def rename(_old: str, _new: str) -> dict[str, Any]:
        return {"handled": True}

    async def scaffold(_name: str, _title: str) -> dict[str, Any]:
        return {"handled": True}

    async def deprecate(_name: str) -> dict[str, Any]:
        return {"handled": True}

    async def noargs() -> dict[str, Any]:
        return {"handled": True}

    return OrchestratorCommandWorker(
        kickoff_ready=kickoff_ready,
        apply_carve_ready=apply_carve_ready,
        capture_submit=dict_result,
        retry_failed=ticket_str,
        set_schedule_at=ticket_schedule,
        update_metadata=ticket_metadata,
        force_status=ticket_status,
        note_ensure=note_name,
        note_retire=note_name,
        send_agent_message=agent_message,
        send_agent_key=agent_key,
        refresh_agent_transcript=ticket_str,
        interrupt_agent=ticket_str,
        stop_agent=ticket_str,
        rename_rogue=rename,
        scaffold_plan=scaffold,
        rename_plan=rename,
        deprecate_plan=deprecate,
        quick_kick_ticket=ticket_str,
        quick_create_ticket=lambda _title: {"handled": True},
        spawn_rogue=dict_result,
        reconfigure_collaborator=noargs,
    )


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
