"""``state.*`` read-model application handlers."""

from __future__ import annotations

from typing import Any, cast

from murder.app.protocol.read_models import dto_to_wire
from murder.app.protocol.reads import EmptyParams, NamedReadParams, TicketGetParams
from murder.app.protocol.requests import QueryName
from murder.app.protocol.subscriptions import ProjectionTopic
from murder.app.service.application import ApplicationRegistrar
from murder.app.service.handlers._common import threaded, value
from murder.app.service.projection_registry import ProjectionProviderRegistry
from murder.app.service.read_model import ServiceReadModel


def register(
    app: ApplicationRegistrar,
    projections: ProjectionProviderRegistry,
    read_model: ServiceReadModel,
) -> None:
    def _state_schedule_snapshot(body: dict[str, Any]) -> dict[str, Any]:
        EmptyParams.model_validate(body or {})
        return value(read_model.get_schedule_snapshot())

    def _state_conversations_snapshot(body: dict[str, Any]) -> dict[str, Any]:
        EmptyParams.model_validate(body or {})
        return value(read_model.get_conversations_snapshot())

    def _state_plans_snapshot(body: dict[str, Any]) -> dict[str, Any]:
        EmptyParams.model_validate(body or {})
        return value(read_model.get_plans_snapshot())

    def _state_notes_snapshot(body: dict[str, Any]) -> dict[str, Any]:
        EmptyParams.model_validate(body or {})
        return value(read_model.get_notes_snapshot())

    def _state_reports_snapshot(body: dict[str, Any]) -> dict[str, Any]:
        EmptyParams.model_validate(body or {})
        return value(read_model.get_reports_snapshot())

    def _state_history_snapshot(body: dict[str, Any]) -> dict[str, Any]:
        EmptyParams.model_validate(body or {})
        return value(read_model.get_history_snapshot())

    def _state_transit_snapshot(body: dict[str, Any]) -> dict[str, Any]:
        EmptyParams.model_validate(body or {})
        return value(read_model.get_transit_snapshot())

    def _state_ticket_detail(body: dict[str, Any]) -> dict[str, Any]:
        params = TicketGetParams.model_validate(body)
        try:
            return value(read_model.get_ticket_detail(params.ticket_id))
        except KeyError:
            return value(None)

    def _state_plan_display(body: dict[str, Any]) -> dict[str, Any]:
        params = NamedReadParams.model_validate(body)
        return value(read_model.get_plan_display(params.name))

    def _state_note_display(body: dict[str, Any]) -> dict[str, Any]:
        params = NamedReadParams.model_validate(body)
        return value(read_model.get_note_display(params.name))

    def _state_report_display(body: dict[str, Any]) -> dict[str, Any]:
        params = NamedReadParams.model_validate(body)
        return value(read_model.get_report_display(params.name))

    def _state_harness_models_snapshot(body: dict[str, Any]) -> dict[str, Any]:
        EmptyParams.model_validate(body or {})
        return value(read_model.get_harness_models_snapshot())

    # These read-model handlers do blocking sqlite/git/file work and are
    # offloaded to worker threads so the application socket can keep answering
    # frontend reads during boot. They are thread-safe
    # because ``ServiceReadModel`` opens a fresh scoped database connection for
    # every call — no shared long-lived ``runtime.db`` connection is touched
    # across threads.
    app.register_application_query(QueryName.SCHEDULE_GET, threaded(_state_schedule_snapshot))
    app.register_application_query(
        QueryName.CONVERSATIONS_GET, threaded(_state_conversations_snapshot)
    )
    app.register_application_query(QueryName.PLANS_LIST, threaded(_state_plans_snapshot))
    app.register_application_query(QueryName.NOTES_LIST, threaded(_state_notes_snapshot))
    app.register_application_query(QueryName.REPORTS_LIST, threaded(_state_reports_snapshot))
    app.register_application_query(QueryName.HISTORY_LIST, threaded(_state_history_snapshot))
    app.register_application_query(QueryName.TRANSIT_GET, threaded(_state_transit_snapshot))
    app.register_application_query(QueryName.TICKET_GET, threaded(_state_ticket_detail))
    app.register_application_query(QueryName.PLAN_GET, threaded(_state_plan_display))
    app.register_application_query(QueryName.NOTE_GET, threaded(_state_note_display))
    app.register_application_query(QueryName.REPORT_GET, threaded(_state_report_display))
    app.register_application_query(
        QueryName.HARNESS_MODELS_LIST, threaded(_state_harness_models_snapshot)
    )

    def _conversations_projection() -> dict[str, object]:
        return cast(
            dict[str, object],
            dto_to_wire(read_model.get_conversations_snapshot()),
        )

    def _schedule_projection() -> dict[str, object]:
        return cast(
            dict[str, object],
            dto_to_wire(read_model.get_schedule_snapshot()),
        )

    projections.register(
        ProjectionTopic.CONVERSATIONS,
        _conversations_projection,
    )
    projections.register(
        ProjectionTopic.SCHEDULE,
        _schedule_projection,
    )
