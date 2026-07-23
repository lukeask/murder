"""``plan.*`` application handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.protocol.lifecycle import PlanCreateParams
from murder.app.protocol.requests import CommandName
from murder.app.service.handlers._common import require_orchestrator

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    async def _plan_create(body: dict[str, Any]) -> dict[str, Any]:
        params = PlanCreateParams.model_validate(body)
        if not params.plan_name and not params.auto_name:
            raise ValueError("plan.create requires plan_name or auto_name")
        return await require_orchestrator(host).create_plan(
            params.plan_name,
            params.message,
            body=params.body,
            auto_name=params.auto_name,
        )

    host.register_application_command(CommandName.PLAN_CREATE, _plan_create)
