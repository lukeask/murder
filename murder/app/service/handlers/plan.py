"""``plan.*`` application handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.protocol.requests import CommandName
from murder.app.service.handlers._common import require_orchestrator

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    async def _plan_create(body: dict[str, Any]) -> dict[str, Any]:
        plan_name = str(body.get("plan_name", "")).strip()
        auto_name = bool(body.get("auto_name", False))
        if not plan_name and not auto_name:
            raise ValueError("plan.create requires plan_name or auto_name")
        message = str(body.get("message", ""))
        plan_body = body.get("body")
        return await require_orchestrator(host).create_plan(
            plan_name,
            message,
            body=plan_body if isinstance(plan_body, str) else None,
            auto_name=auto_name,
        )

    host.register_application_command(CommandName.PLAN_CREATE, _plan_create)
