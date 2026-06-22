"""``state.*`` read-model snapshot RPC handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from murder.app.service.handlers._common import require_read_model, threaded, value

if TYPE_CHECKING:
    from murder.app.service.host import ServiceHost


def register(host: ServiceHost) -> None:
    def _state_schedule_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
        return value(require_read_model(host).get_schedule_snapshot())

    def _state_crow_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
        return value(require_read_model(host).get_crow_snapshot())

    def _state_conversations_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
        return value(require_read_model(host).get_conversations_snapshot())

    def _state_plans_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
        return value(require_read_model(host).get_plans_snapshot())

    def _state_notes_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
        return value(require_read_model(host).get_notes_snapshot())

    def _state_reports_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
        return value(require_read_model(host).get_reports_snapshot())

    def _state_history_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
        return value(require_read_model(host).get_history_snapshot())

    def _state_transit_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
        return value(require_read_model(host).get_transit_snapshot())

    def _state_ticket_detail(body: dict[str, Any]) -> dict[str, Any]:
        ticket_id = str(body.get("ticket_id", "")).strip()
        if not ticket_id:
            raise ValueError("state.ticket_detail requires ticket_id")
        try:
            return value(require_read_model(host).get_ticket_detail(ticket_id))
        except KeyError:
            return value(None)

    def _state_plan_display(body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name", "")).strip()
        if not name:
            raise ValueError("state.plan_display requires name")
        return value(require_read_model(host).get_plan_display(name))

    def _state_note_display(body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name", "")).strip()
        if not name:
            raise ValueError("state.note_display requires name")
        return value(require_read_model(host).get_note_display(name))

    def _state_report_display(body: dict[str, Any]) -> dict[str, Any]:
        name = str(body.get("name", "")).strip()
        if not name:
            raise ValueError("state.report_display requires name")
        return value(require_read_model(host).get_report_display(name))

    def _state_harness_models_snapshot(_body: dict[str, Any]) -> dict[str, Any]:
        return value(require_read_model(host).get_harness_models_snapshot())

    # These read-model handlers do blocking sqlite/git/file work and are
    # offloaded to worker threads via ``threaded`` so the bus socket can
    # keep answering frontend reads during boot. They are thread-safe
    # because ``ServiceReadModel`` opens a FRESH per-call sqlite connection
    # (``get_db`` with ``check_same_thread=False``) — no shared connection
    # is touched across threads.
    host.register_rpc_handler(
        "state.schedule_snapshot", threaded(_state_schedule_snapshot)
    )
    host.register_rpc_handler("state.crow_snapshot", threaded(_state_crow_snapshot))
    host.register_rpc_handler(
        "state.conversations_snapshot", threaded(_state_conversations_snapshot)
    )
    host.register_rpc_handler("state.plans_snapshot", threaded(_state_plans_snapshot))
    host.register_rpc_handler("state.notes_snapshot", threaded(_state_notes_snapshot))
    host.register_rpc_handler(
        "state.reports_snapshot", threaded(_state_reports_snapshot)
    )
    host.register_rpc_handler(
        "state.history_snapshot", threaded(_state_history_snapshot)
    )
    host.register_rpc_handler(
        "state.transit_snapshot", threaded(_state_transit_snapshot)
    )
    host.register_rpc_handler("state.ticket_detail", threaded(_state_ticket_detail))
    host.register_rpc_handler("state.plan_display", threaded(_state_plan_display))
    host.register_rpc_handler("state.note_display", threaded(_state_note_display))
    host.register_rpc_handler("state.report_display", threaded(_state_report_display))
    host.register_rpc_handler(
        "state.harness_models_snapshot",
        threaded(_state_harness_models_snapshot),
    )
