"""``ticket.*`` RPC handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.protocol.requests import CommandName, QueryName
from murder.app.service.handlers._common import require_orchestrator

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    def _ticket_next_id(_body: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "ticket_id": require_orchestrator(host).next_ticket_id()}

    def _ticket_exists(body: dict[str, Any]) -> dict[str, Any]:
        handle = str(body.get("handle", "")).strip()
        if not handle:
            raise ValueError("ticket.exists requires handle")
        return {"ok": True, "exists": require_orchestrator(host).ticket_exists(handle)}

    async def _ticket_save_body(body: dict[str, Any]) -> dict[str, Any]:
        ticket_id = str(body.get("ticket_id", "")).strip()
        if not ticket_id:
            raise ValueError("ticket.save_body requires ticket_id")
        md = body.get("body")
        if not isinstance(md, str):
            raise ValueError("ticket.save_body requires body string")
        return await require_orchestrator(host).save_ticket_body(ticket_id, md)

    async def _ticket_schedule(body: dict[str, Any]) -> dict[str, Any]:
        ticket_id = str(body.get("ticket_id", "")).strip()
        if not ticket_id:
            raise ValueError("ticket.schedule requires ticket_id")
        duration = str(body.get("duration", ""))
        return await require_orchestrator(host).schedule_ticket(ticket_id, duration)

    host.register_application_query(QueryName.TICKET_NEXT_ID, _ticket_next_id)
    host.register_application_query(QueryName.TICKET_EXISTS, _ticket_exists)
    host.register_application_command(CommandName.TICKET_SAVE_BODY, _ticket_save_body)
    host.register_application_command(CommandName.TICKET_SCHEDULE, _ticket_schedule)
