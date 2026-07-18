"""Application-gateway adapter over transitional service internals.

Clients can select only the closed capabilities declared in
``murder.app.protocol.requests``.  The stringly RPC/worker bus remains behind
this adapter and can be removed without another client protocol change.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from murder.app.protocol.requests import (
    CommandName,
    CommandRequest,
    OrchestrationAction,
    QueryName,
    QueryRequest,
)
from murder.bus.broker import DurableBroker

QUERY_TARGETS: Mapping[QueryName, str] = {
    QueryName.HEALTH_GET: "health.ping",
    QueryName.COMMAND_GET: "command.status",
    QueryName.CONVERSATIONS_GET: "state.conversations_snapshot",
    QueryName.ROSTER_GET: "state.crow_snapshot",
    QueryName.SCHEDULE_GET: "state.schedule_snapshot",
    QueryName.PLANS_LIST: "state.plans_snapshot",
    QueryName.NOTES_LIST: "state.notes_snapshot",
    QueryName.REPORTS_LIST: "state.reports_snapshot",
    QueryName.HISTORY_LIST: "state.history_snapshot",
    QueryName.TRANSIT_GET: "state.transit_snapshot",
    QueryName.TICKET_GET: "state.ticket_detail",
    QueryName.PLAN_GET: "state.plan_display",
    QueryName.NOTE_GET: "state.note_display",
    QueryName.REPORT_GET: "state.report_display",
    QueryName.HARNESS_MODELS_LIST: "state.harness_models_snapshot",
    QueryName.TICKET_NEXT_ID: "ticket.next_id",
    QueryName.TICKET_EXISTS: "ticket.exists",
    QueryName.SETTINGS_GET: "settings.get",
    QueryName.WORKTREES_LIST: "worktree.list",
    QueryName.FAVORITES_GET: "tui.load_favorites",
    QueryName.SPAWN_FAVORITES_GET: "tui.load_spawn_favorites",
    QueryName.TEMPLATES_GET: "tui.load_templates",
    QueryName.THEMES_GET: "tui.load_themes",
    QueryName.WORKFLOWS_GET: "tui.load_workflows",
}

COMMAND_TARGETS: Mapping[CommandName, str] = {
    CommandName.HARNESS_ANSWER: "harness_control.answer_structured",
    CommandName.IMAGE_UPLOAD: "image.upload",
    CommandName.TICKET_SAVE_BODY: "ticket.save_body",
    CommandName.TICKET_SCHEDULE: "ticket.schedule",
    CommandName.PLAN_CREATE: "plan.create",
    CommandName.SETTINGS_UPDATE: "settings.update",
    CommandName.LLM_SETTINGS_SET_DISABLED: "llm.settings.set_disabled",
    CommandName.LLM_PROVIDER_CREATE: "llm.provider.create",
    CommandName.LLM_PROVIDER_UPDATE: "llm.provider.update",
    CommandName.LLM_PROVIDER_DELETE: "llm.provider.delete",
    CommandName.LLM_PROVIDER_MODELS_UPDATE: "llm.provider.models.update",
    CommandName.LLM_PROVIDER_DISCOVER_MODELS: "llm.provider.discover_models",
    CommandName.LLM_POLICY_CREATE: "llm.policy.create",
    CommandName.LLM_POLICY_UPDATE: "llm.policy.update",
    CommandName.LLM_POLICY_DELETE: "llm.policy.delete",
    CommandName.LLM_POLICY_ACTIVATE: "llm.policy.activate",
    CommandName.LLM_POLICY_CLONE: "llm.policy.clone",
    CommandName.LLM_FEATURE_POLICY_SET: "llm.feature_policy.set",
    CommandName.LLM_PREVIEW_RESOLUTION: "llm.preview_resolution",
    CommandName.FAVORITES_SET: "tui.save_favorites",
    CommandName.SPAWN_FAVORITES_SET: "tui.save_spawn_favorites",
    CommandName.TEMPLATES_SET: "tui.save_templates",
    CommandName.THEMES_SET: "tui.save_themes",
    CommandName.THEME_IMPORT: "tui.import_theme",
    CommandName.WORKFLOWS_SET: "tui.save_workflows",
    CommandName.WORKFLOW_START: "tui.run_workflow",
}


class ApplicationGateway:
    """Dispatch the closed public request union into compatibility handlers."""

    def __init__(self, broker: DurableBroker) -> None:
        self._broker = broker

    async def request(
        self,
        request: QueryRequest | CommandRequest,
        *,
        timeout_s: float,
    ) -> dict[str, Any]:
        params = dict(request.params)
        if isinstance(request, QueryRequest):
            target = QUERY_TARGETS[request.name]
        elif request.name is CommandName.ORCHESTRATION_EXECUTE:
            target = "command.submit"
            params = self._orchestration_command(params)
        else:
            target = COMMAND_TARGETS[request.name]
        result = await self._broker.request(target, params, timeout_s=timeout_s)
        return cast(dict[str, Any], result)

    @staticmethod
    def _orchestration_command(params: dict[str, object]) -> dict[str, object]:
        """Hide the internal worker address from the public command contract."""

        kind = params.get("kind")
        payload = params.get("payload", {})
        if not isinstance(kind, str) or not kind:
            raise ValueError("orchestration.execute requires a non-empty kind")
        action = OrchestrationAction(kind)
        if not isinstance(payload, dict):
            raise ValueError("orchestration.execute payload must be an object")
        out = dict(params)
        out["kind"] = action.value
        out["target_worker"] = "orchestrator"
        return out
