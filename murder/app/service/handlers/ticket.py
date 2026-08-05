"""``ticket.*`` application handlers."""

from __future__ import annotations

from typing import Any

from murder.app.protocol.lifecycle import (
    TicketExistsParams,
    TicketExistsResult,
    TicketNextIdParams,
    TicketNextIdResult,
    TicketSaveBodyParams,
    TicketScheduleParams,
)
from murder.app.protocol.requests import CommandName, QueryName
from murder.app.service.application import ApplicationRegistrar
from murder.runtime.orchestration.orchestrator import Orchestrator


def register(app: ApplicationRegistrar, orchestrator: Orchestrator) -> None:
    def _ticket_next_id(body: dict[str, Any]) -> dict[str, Any]:
        TicketNextIdParams.model_validate(body or {})
        return TicketNextIdResult(
            ok=True, ticket_id=orchestrator.next_ticket_id()
        ).model_dump(mode="json")

    def _ticket_exists(body: dict[str, Any]) -> dict[str, Any]:
        params = TicketExistsParams.model_validate(body)
        return TicketExistsResult(
            ok=True, exists=orchestrator.ticket_exists(params.handle)
        ).model_dump(mode="json")

    async def _ticket_save_body(body: dict[str, Any]) -> dict[str, Any]:
        params = TicketSaveBodyParams.model_validate(body)
        return await orchestrator.save_ticket_body(params.ticket_id, params.body)

    async def _ticket_schedule(body: dict[str, Any]) -> dict[str, Any]:
        params = TicketScheduleParams.model_validate(body)
        return await orchestrator.schedule_ticket(params.ticket_id, params.duration)

    app.register_application_query(QueryName.TICKET_NEXT_ID, _ticket_next_id)
    app.register_application_query(QueryName.TICKET_EXISTS, _ticket_exists)
    app.register_application_command(CommandName.TICKET_SAVE_BODY, _ticket_save_body)
    app.register_application_command(CommandName.TICKET_SCHEDULE, _ticket_schedule)
