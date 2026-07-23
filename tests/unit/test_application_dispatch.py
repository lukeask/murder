"""Direct application dispatch never enters the legacy broker path."""

from __future__ import annotations

from typing import Any

import pytest

from murder.app.protocol.requests import CommandName, QueryName
from murder.app.service.application import ApplicationDispatcher, ApplicationHandler


class _Orchestrator:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str, str | None]] = []

    async def send_agent_message(
        self, agent_id: str, message: str, ticket_id: str | None
    ) -> dict[str, Any]:
        self.messages.append((agent_id, message, ticket_id))
        return {"handled": True, "delivered": True}


def _handler_for(capability: QueryName | CommandName) -> ApplicationHandler:
    async def _handle(params: dict[str, Any]) -> dict[str, Any]:
        return {"capability": capability.value, "params": params}

    return _handle


def _dispatcher(orchestrator: _Orchestrator) -> ApplicationDispatcher:
    async def _orchestration(params: dict[str, Any]) -> dict[str, Any]:
        payload = params["payload"]
        assert isinstance(payload, dict)
        return await orchestrator.send_agent_message(
            str(payload["agent_id"]),
            str(payload["message"]),
            None,
        )

    return ApplicationDispatcher(
        queries={name: _handler_for(name) for name in QueryName},
        commands={
            name: _handler_for(name)
            for name in CommandName
            if name is not CommandName.ORCHESTRATION_EXECUTE
        },
        orchestration=_orchestration,
    )


@pytest.mark.asyncio
async def test_query_invokes_enum_bound_feature_handler() -> None:
    dispatcher = _dispatcher(_Orchestrator())

    result = await dispatcher.query(QueryName.ROSTER_GET, {"fresh": True})

    assert result == {
        "capability": "roster.get",
        "params": {"fresh": True},
    }


@pytest.mark.asyncio
async def test_orchestration_invokes_feature_without_command_event() -> None:
    orchestrator = _Orchestrator()
    dispatcher = _dispatcher(orchestrator)

    result = await dispatcher.command(
        CommandName.ORCHESTRATION_EXECUTE,
        {
            "kind": "agent.message",
            "payload": {"agent_id": "crow-1", "message": "hello"},
        },
    )

    assert result == {"handled": True, "delivered": True}
    assert orchestrator.messages == [("crow-1", "hello", None)]
