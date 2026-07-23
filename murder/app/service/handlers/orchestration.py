"""Application commands implemented by the runtime orchestrator."""

from __future__ import annotations

from typing import Any

from murder.app.protocol.requests import CommandName
from murder.app.service.application import ApplicationRegistrar
from murder.runtime.workers.orchestrator_worker import (
    OrchestratorCommands,
    dispatch_orchestrator_command,
)

_COMMANDS = (
    CommandName.AGENT_INTERRUPT,
    CommandName.AGENT_MESSAGE,
    CommandName.AGENT_RESUME_FROM_HISTORY,
    CommandName.AGENT_SEND_KEY,
    CommandName.AGENT_STOP,
    CommandName.CROW_RENAME_ROGUE,
    CommandName.CROW_RESET,
    CommandName.CROW_SPAWN_ROGUE,
    CommandName.HISTORY_DISMISS,
    CommandName.NOTETAKER_CAPTURE_SUBMIT,
    CommandName.PLAN_RENAME,
    CommandName.PLANNER_SPAWN,
    CommandName.TICKET_QUICK_CREATE,
)


def register(app: ApplicationRegistrar, orchestrator: OrchestratorCommands) -> None:
    """Bind the closed application vocabulary to orchestration effects."""

    for command_name in _COMMANDS:

        async def _execute(
            body: dict[str, Any],
            name: CommandName = command_name,
        ) -> dict[str, Any]:
            return await dispatch_orchestrator_command(orchestrator, name, body)

        app.register_application_command(command_name, _execute)


__all__ = ["register"]
