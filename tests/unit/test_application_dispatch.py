"""Direct application dispatch never enters the legacy broker path."""

from __future__ import annotations

from typing import Any

import pytest

from murder.app.protocol.requests import CommandName, QueryName
from murder.app.service.application import ApplicationDispatcher, ApplicationHandler
from murder.app.service.handlers import register_all


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
    async def _agent_message(params: dict[str, Any]) -> dict[str, Any]:
        return await orchestrator.send_agent_message(
            str(params["agent_id"]),
            str(params["message"]),
            None,
        )

    return ApplicationDispatcher(
        queries={name: _handler_for(name) for name in QueryName},
        commands={
            name: (_agent_message if name is CommandName.AGENT_MESSAGE else _handler_for(name))
            for name in CommandName
        },
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
async def test_direct_command_invokes_feature_without_command_event() -> None:
    orchestrator = _Orchestrator()
    dispatcher = _dispatcher(orchestrator)

    result = await dispatcher.command(
        CommandName.AGENT_MESSAGE,
        {
            "agent_id": "crow-1", "message": "hello",
        },
    )

    assert result == {"handled": True, "delivered": True}
    assert orchestrator.messages == [("crow-1", "hello", None)]


def test_feature_composition_registers_every_closed_operation_by_enum() -> None:
    class _RegistrationHost:
        def __init__(self) -> None:
            self.queries: dict[QueryName, ApplicationHandler] = {}
            self.commands: dict[CommandName, ApplicationHandler] = {}

        def register_application_query(
            self, name: QueryName, handler: ApplicationHandler
        ) -> None:
            self.queries[name] = handler

        def register_application_command(
            self, name: CommandName, handler: ApplicationHandler
        ) -> None:
            self.commands[name] = handler

    host = _RegistrationHost()
    register_all(host)  # type: ignore[arg-type]

    assert set(host.queries) == set(QueryName)
    assert set(host.commands) < set(CommandName)
