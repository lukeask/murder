"""Closed request/reply contracts exposed to Murder clients.

Names describe product capabilities.  They deliberately do not contain
worker targets or arbitrary RPC handler names; the service gateway owns the
mapping to transitional internals.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from murder.app.protocol.common import ApplicationModel, StrEnum


class QueryName(StrEnum):
    HEALTH_GET = "health.get"
    COMMAND_GET = "command.get"
    CONVERSATIONS_GET = "conversations.get"
    ROSTER_GET = "roster.get"
    SCHEDULE_GET = "schedule.get"
    PLANS_LIST = "plans.list"
    NOTES_LIST = "notes.list"
    REPORTS_LIST = "reports.list"
    HISTORY_LIST = "history.list"
    TRANSIT_GET = "transit.get"
    TICKET_GET = "ticket.get"
    PLAN_GET = "plan.get"
    NOTE_GET = "note.get"
    REPORT_GET = "report.get"
    HARNESS_MODELS_LIST = "harness_models.list"
    TICKET_NEXT_ID = "ticket.next_id"
    TICKET_EXISTS = "ticket.exists"
    SETTINGS_GET = "settings.get"
    WORKTREES_LIST = "worktrees.list"
    FAVORITES_GET = "favorites.get"
    SPAWN_FAVORITES_GET = "spawn_favorites.get"
    TEMPLATES_GET = "templates.get"
    THEMES_GET = "themes.get"
    WORKFLOWS_GET = "workflows.get"
    APPROVALS_LIST = "approvals.list"
    APPROVALS_GET = "approvals.get"
    PERMISSIONS_LIST = "permissions.list"
    SESSION_WRITER_GET = "session.writer.get"


class CommandName(StrEnum):
    ORCHESTRATION_EXECUTE = "orchestration.execute"
    HARNESS_ANSWER = "harness.answer"
    IMAGE_UPLOAD = "image.upload"
    TICKET_SAVE_BODY = "ticket.save_body"
    TICKET_SCHEDULE = "ticket.schedule"
    PLAN_CREATE = "plan.create"
    SETTINGS_UPDATE = "settings.update"
    LLM_SETTINGS_SET_DISABLED = "llm.settings.set_disabled"
    LLM_PROVIDER_CREATE = "llm.provider.create"
    LLM_PROVIDER_UPDATE = "llm.provider.update"
    LLM_PROVIDER_DELETE = "llm.provider.delete"
    LLM_PROVIDER_MODELS_UPDATE = "llm.provider.models.update"
    LLM_PROVIDER_DISCOVER_MODELS = "llm.provider.discover_models"
    LLM_POLICY_CREATE = "llm.policy.create"
    LLM_POLICY_UPDATE = "llm.policy.update"
    LLM_POLICY_DELETE = "llm.policy.delete"
    LLM_POLICY_ACTIVATE = "llm.policy.activate"
    LLM_POLICY_CLONE = "llm.policy.clone"
    LLM_FEATURE_POLICY_SET = "llm.feature_policy.set"
    LLM_PREVIEW_RESOLUTION = "llm.preview_resolution"
    FAVORITES_SET = "favorites.set"
    SPAWN_FAVORITES_SET = "spawn_favorites.set"
    TEMPLATES_SET = "templates.set"
    THEMES_SET = "themes.set"
    THEME_IMPORT = "theme.import"
    WORKFLOWS_SET = "workflows.set"
    WORKFLOW_START = "workflow.start"
    TRIGGER_FIRE = "trigger.fire"
    APPROVAL_DECIDE = "approval.decide"
    SESSION_WRITER_ACQUIRE = "session.writer.acquire"
    SESSION_WRITER_RENEW = "session.writer.renew"
    SESSION_WRITER_RELEASE = "session.writer.release"


class OrchestrationAction(StrEnum):
    AGENT_INTERRUPT = "agent.interrupt"
    AGENT_MESSAGE = "agent.message"
    AGENT_RESUME_FROM_HISTORY = "agent.resume_from_history"
    AGENT_SEND_KEY = "agent.send_key"
    AGENT_STOP = "agent.stop"
    CROW_RENAME_ROGUE = "crow.rename_rogue"
    CROW_RESET = "crow.reset"
    CROW_SPAWN_ROGUE = "crow.spawn_rogue"
    HISTORY_DISMISS = "history.dismiss"
    NOTETAKER_CAPTURE_SUBMIT = "notetaker.capture.submit"
    PLAN_RENAME = "plan.rename"
    PLANNER_SPAWN = "planner.spawn"
    SCHEDULER_SET_STEERING = "scheduler.set_steering"
    HARNESS_USAGE_SAMPLE = "state.harness_usage.sample"
    TICKET_QUICK_CREATE = "ticket.quick_create"


class QueryRequest(ApplicationModel):
    kind: Literal["query"] = "query"
    name: QueryName
    params: dict[str, object] = Field(default_factory=dict)


class CommandRequest(ApplicationModel):
    kind: Literal["command"] = "command"
    name: CommandName
    params: dict[str, object] = Field(default_factory=dict)


class RequestResult(ApplicationModel):
    value: dict[str, object] = Field(default_factory=dict)
