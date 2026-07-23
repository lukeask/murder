"""``ticket.*`` application handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.protocol.lifecycle import (
    TicketExistsParams,
    TicketExistsResult,
    TicketNextIdParams,
    TicketNextIdResult,
    TicketSaveBodyParams,
    TicketScheduleParams,
)
from murder.app.protocol.requests import CommandName, QueryName
from murder.app.service.handlers._common import require_orchestrator

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    def _ticket_next_id(body: dict[str, Any]) -> dict[str, Any]:
        TicketNextIdParams.model_validate(body or {})
        return TicketNextIdResult(
            ok=True, ticket_id=require_orchestrator(host).next_ticket_id()
        ).model_dump(mode="json")

    def _ticket_exists(body: dict[str, Any]) -> dict[str, Any]:
        params = TicketExistsParams.model_validate(body)
        return TicketExistsResult(
            ok=True, exists=require_orchestrator(host).ticket_exists(params.handle)
        ).model_dump(mode="json")

    async def _ticket_save_body(body: dict[str, Any]) -> dict[str, Any]:
        params = TicketSaveBodyParams.model_validate(body)
        return await require_orchestrator(host).save_ticket_body(params.ticket_id, params.body)

    async def _ticket_schedule(body: dict[str, Any]) -> dict[str, Any]:
        params = TicketScheduleParams.model_validate(body)
        return await require_orchestrator(host).schedule_ticket(params.ticket_id, params.duration)

    host.register_application_query(QueryName.TICKET_NEXT_ID, _ticket_next_id)
    host.register_application_query(QueryName.TICKET_EXISTS, _ticket_exists)
    host.register_application_command(CommandName.TICKET_SAVE_BODY, _ticket_save_body)
    host.register_application_command(CommandName.TICKET_SCHEDULE, _ticket_schedule)
