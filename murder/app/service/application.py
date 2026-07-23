"""Typed, in-process application dispatch.

The public application protocol terminates here.  A request is selected by its
closed enum and invokes the feature handler directly; no bus event, RPC target,
or broker participates in normal application dispatch.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from murder.app.protocol.requests import CommandName, QueryName

ApplicationHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


class ApplicationPort(Protocol):
    """Use-case port consumed by :class:`ApplicationGateway`."""

    async def query(self, name: QueryName, params: dict[str, Any]) -> dict[str, Any]: ...

    async def command(self, name: CommandName, params: dict[str, Any]) -> dict[str, Any]: ...


# These bindings are composition metadata, not runtime RPC addresses.  They
# disappear as each handler module starts exporting its enum-keyed feature
# handlers directly.  ApplicationDispatcher resolves them once at startup and
# retains only typed enum -> callable bindings.
_QUERY_HANDLER_BINDINGS: Mapping[QueryName, str] = {
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
    QueryName.APPROVALS_LIST: "approvals.list",
    QueryName.APPROVALS_GET: "approvals.get",
    QueryName.PERMISSIONS_LIST: "permissions.list",
    QueryName.SESSION_WRITER_GET: "session.writer.get",
    QueryName.WORKFLOW_RUNS_LIST: "workflow.runs.list",
    QueryName.WORKFLOW_RUNS_GET: "workflow.runs.get",
}

_COMMAND_HANDLER_BINDINGS: Mapping[CommandName, str] = {
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
    CommandName.TRIGGER_FIRE: "trigger.fire",
    CommandName.APPROVAL_DECIDE: "approval.decide",
    CommandName.SESSION_WRITER_ACQUIRE: "session.writer.acquire",
    CommandName.SESSION_WRITER_RENEW: "session.writer.renew",
    CommandName.SESSION_WRITER_RELEASE: "session.writer.release",
    CommandName.SESSION_COMMAND_EXECUTE: "session.command.execute",
    CommandName.WORKFLOW_SIGNAL: "workflow.signal",
}


class ApplicationDispatcher:
    """Enum-keyed application service composed from feature handlers."""

    def __init__(
        self,
        *,
        queries: Mapping[QueryName, ApplicationHandler],
        commands: Mapping[CommandName, ApplicationHandler],
        orchestration: ApplicationHandler,
    ) -> None:
        missing_queries = set(QueryName) - set(queries)
        missing_commands = set(CommandName) - set(commands) - {CommandName.ORCHESTRATION_EXECUTE}
        if missing_queries or missing_commands:
            raise RuntimeError(
                "incomplete application dispatch: "
                f"queries={sorted(item.value for item in missing_queries)}, "
                f"commands={sorted(item.value for item in missing_commands)}"
            )
        self._queries = dict(queries)
        self._commands = dict(commands)
        self._orchestration = orchestration

    @property
    def available_queries(self) -> tuple[QueryName, ...]:
        return tuple(sorted(self._queries, key=lambda name: name.value))

    @property
    def available_commands(self) -> tuple[CommandName, ...]:
        return tuple(
            sorted(
                (*self._commands, CommandName.ORCHESTRATION_EXECUTE),
                key=lambda name: name.value,
            )
        )

    @classmethod
    def compose(
        cls,
        feature_handlers: Mapping[str, ApplicationHandler],
        *,
        orchestration: ApplicationHandler,
    ) -> ApplicationDispatcher:
        """Resolve startup registrations into direct typed callables once."""

        return cls(
            queries={
                capability: feature_handlers[target]
                for capability, target in _QUERY_HANDLER_BINDINGS.items()
            },
            commands={
                capability: feature_handlers[target]
                for capability, target in _COMMAND_HANDLER_BINDINGS.items()
            },
            orchestration=orchestration,
        )

    async def query(self, name: QueryName, params: dict[str, Any]) -> dict[str, Any]:
        return await _invoke(self._queries[name], params)

    async def command(self, name: CommandName, params: dict[str, Any]) -> dict[str, Any]:
        if name is CommandName.ORCHESTRATION_EXECUTE:
            return await _invoke(self._orchestration, params)
        return await _invoke(self._commands[name], params)


async def _invoke(handler: ApplicationHandler, params: dict[str, Any]) -> dict[str, Any]:
    result = handler(params)
    if inspect.isawaitable(result):
        result = await result
    return result


__all__ = ["ApplicationDispatcher", "ApplicationHandler", "ApplicationPort"]
