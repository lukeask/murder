"""``plan.*`` application handlers."""

from __future__ import annotations

from typing import Any

from murder.app.protocol.lifecycle import PlanCreateParams
from murder.app.protocol.requests import CommandName
from murder.app.service.application import ApplicationRegistrar
from murder.runtime.orchestration.orchestrator import Orchestrator


def register(app: ApplicationRegistrar, orchestrator: Orchestrator) -> None:
    async def _plan_create(body: dict[str, Any]) -> dict[str, Any]:
        params = PlanCreateParams.model_validate(body)
        if not params.plan_name and not params.auto_name:
            raise ValueError("plan.create requires plan_name or auto_name")
        return await orchestrator.create_plan(
            params.plan_name,
            params.message,
            body=params.body,
            auto_name=params.auto_name,
        )

    app.register_application_command(CommandName.PLAN_CREATE, _plan_create)
