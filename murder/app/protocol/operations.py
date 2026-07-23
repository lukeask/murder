"""Single source of truth for public application operations.

An operation binds one closed capability name to the Pydantic models accepted
and returned at the public boundary.  Transports, capability negotiation and
TypeScript generation must use this registry rather than independently
maintained name lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field, JsonValue, RootModel

from murder.app.protocol.common import ApplicationModel
from murder.app.protocol.permissions import (
    DecideApprovalParams,
    DecideApprovalResult,
    GetApprovalParams,
    GetApprovalResult,
    ListApprovalsParams,
    ListApprovalsResult,
    ListPermissionsParams,
    ListPermissionsResult,
)
from murder.app.protocol.read_models import CrowSnapshot
from murder.app.protocol.requests import CommandName, QueryName
from murder.app.protocol.sessions import (
    AcquireWriterLeaseParams,
    ExecuteSessionCommandParams,
    ExecuteSessionCommandResult,
    GetWriterLeaseParams,
    GetWriterLeaseResult,
    ReleaseWriterLeaseParams,
    RenewWriterLeaseParams,
    WriterLeaseResult,
)
from murder.app.protocol.workflows import (
    GetWorkflowRunParams,
    GetWorkflowRunResult,
    GetWorkflowsParams,
    GetWorkflowsResult,
    ListWorkflowRunsParams,
    ListWorkflowRunsResult,
    SetWorkflowsParams,
    SetWorkflowsResult,
    SignalWorkflowParams,
    SignalWorkflowResult,
    StartWorkflowParams,
    StartWorkflowResult,
)

Name = TypeVar("Name", QueryName, CommandName)
Params = TypeVar("Params")
Result = TypeVar("Result")


class JsonObject(RootModel[dict[str, JsonValue]]):
    """Temporary adapter for legacy handlers not yet given a domain DTO.

    This is deliberately a Pydantic model (rather than an unvalidated ``dict``)
    so every operation travels through the same validation and generation path.
    New operations must use a named model; ``legacy=True`` makes remaining
    compatibility endpoints visible to tests and capability composition.
    """


class EmptyParams(ApplicationModel):
    """An operation which accepts no caller supplied fields."""


class RosterGetParams(EmptyParams):
    """``roster.get`` is deliberately argument-free.

    The roster implementation remains a read-model adapter for now; this DTO
    is the seam that lets its implementation move without widening the public
    request contract.
    """


class TicketGetParams(ApplicationModel):
    ticket_id: str = Field(min_length=1)


class NamedReadParams(ApplicationModel):
    name: str = Field(min_length=1)


@dataclass(frozen=True)
class Operation(Generic[Name, Params, Result]):
    name: Name
    params_model: object
    result_model: object
    legacy: bool = False


# Typed contracts.  A newly added enum member must land here with a named DTO
# or in the matching LEGACY_* set below; silent JsonObject fallback is forbidden.
_QUERY_MODELS: dict[QueryName, tuple[type[BaseModel], type[BaseModel], bool]] = {
    QueryName.SESSION_WRITER_GET: (GetWriterLeaseParams, GetWriterLeaseResult, False),
    QueryName.APPROVALS_LIST: (ListApprovalsParams, ListApprovalsResult, False),
    QueryName.APPROVALS_GET: (GetApprovalParams, GetApprovalResult, False),
    QueryName.PERMISSIONS_LIST: (ListPermissionsParams, ListPermissionsResult, False),
    QueryName.ROSTER_GET: (RosterGetParams, CrowSnapshot, False),
    QueryName.WORKFLOWS_GET: (GetWorkflowsParams, GetWorkflowsResult, False),
    QueryName.WORKFLOW_RUNS_LIST: (ListWorkflowRunsParams, ListWorkflowRunsResult, False),
    QueryName.WORKFLOW_RUNS_GET: (GetWorkflowRunParams, GetWorkflowRunResult, False),
}
_COMMAND_MODELS: dict[CommandName, tuple[type[BaseModel], type[BaseModel], bool]] = {
    CommandName.SESSION_WRITER_ACQUIRE: (AcquireWriterLeaseParams, WriterLeaseResult, False),
    CommandName.SESSION_WRITER_RENEW: (RenewWriterLeaseParams, WriterLeaseResult, False),
    CommandName.SESSION_WRITER_RELEASE: (ReleaseWriterLeaseParams, WriterLeaseResult, False),
    CommandName.SESSION_COMMAND_EXECUTE: (
        ExecuteSessionCommandParams,
        ExecuteSessionCommandResult,
        False,
    ),
    CommandName.APPROVAL_DECIDE: (DecideApprovalParams, DecideApprovalResult, False),
    CommandName.WORKFLOWS_SET: (SetWorkflowsParams, SetWorkflowsResult, False),
    CommandName.WORKFLOW_START: (StartWorkflowParams, StartWorkflowResult, False),
    CommandName.WORKFLOW_SIGNAL: (SignalWorkflowParams, SignalWorkflowResult, False),
}

# Explicitly opaque operations.  Membership here is the only way an enum name
# may use JsonObject; missing coverage fails import via the assertions below.
LEGACY_QUERY_OPERATIONS: frozenset[QueryName] = frozenset(
    {
        QueryName.HEALTH_GET,
        QueryName.COMMAND_GET,
        QueryName.CONVERSATIONS_GET,
        QueryName.SCHEDULE_GET,
        QueryName.PLANS_LIST,
        QueryName.NOTES_LIST,
        QueryName.REPORTS_LIST,
        QueryName.HISTORY_LIST,
        QueryName.TRANSIT_GET,
        QueryName.TICKET_GET,
        QueryName.PLAN_GET,
        QueryName.NOTE_GET,
        QueryName.REPORT_GET,
        QueryName.HARNESS_MODELS_LIST,
        QueryName.TICKET_NEXT_ID,
        QueryName.TICKET_EXISTS,
        QueryName.SETTINGS_GET,
        QueryName.WORKTREES_LIST,
        QueryName.FAVORITES_GET,
        QueryName.SPAWN_FAVORITES_GET,
        QueryName.TEMPLATES_GET,
        QueryName.THEMES_GET,
    }
)
LEGACY_COMMAND_OPERATIONS: frozenset[CommandName] = frozenset(
    {
        CommandName.HARNESS_ANSWER,
        CommandName.IMAGE_UPLOAD,
        CommandName.TICKET_SAVE_BODY,
        CommandName.TICKET_SCHEDULE,
        CommandName.PLAN_CREATE,
        CommandName.SETTINGS_UPDATE,
        CommandName.LLM_SETTINGS_SET_DISABLED,
        CommandName.LLM_PROVIDER_CREATE,
        CommandName.LLM_PROVIDER_UPDATE,
        CommandName.LLM_PROVIDER_DELETE,
        CommandName.LLM_PROVIDER_MODELS_UPDATE,
        CommandName.LLM_PROVIDER_DISCOVER_MODELS,
        CommandName.LLM_POLICY_CREATE,
        CommandName.LLM_POLICY_UPDATE,
        CommandName.LLM_POLICY_DELETE,
        CommandName.LLM_POLICY_ACTIVATE,
        CommandName.LLM_POLICY_CLONE,
        CommandName.LLM_FEATURE_POLICY_SET,
        CommandName.LLM_PREVIEW_RESOLUTION,
        CommandName.FAVORITES_SET,
        CommandName.SPAWN_FAVORITES_SET,
        CommandName.TEMPLATES_SET,
        CommandName.THEMES_SET,
        CommandName.THEME_IMPORT,
        CommandName.TRIGGER_FIRE,
        CommandName.AGENT_INTERRUPT,
        CommandName.AGENT_MESSAGE,
        CommandName.AGENT_RESUME_FROM_HISTORY,
        CommandName.AGENT_SEND_KEY,
        CommandName.AGENT_STOP,
        CommandName.CROW_RENAME_ROGUE,
        CommandName.CROW_RESET,
        CommandName.CROW_SPAWN_ROGUE,
        CommandName.HISTORY_DISMISS,
        CommandName.NOTETAKER_CAPTURE_SUBMIT,
        CommandName.PLAN_RENAME,
        CommandName.PLANNER_SPAWN,
        CommandName.SCHEDULER_SET_STEERING,
        CommandName.HARNESS_USAGE_SAMPLE,
        CommandName.TICKET_QUICK_CREATE,
    }
)

assert set(_QUERY_MODELS).isdisjoint(LEGACY_QUERY_OPERATIONS)
assert set(_COMMAND_MODELS).isdisjoint(LEGACY_COMMAND_OPERATIONS)
assert set(_QUERY_MODELS) | LEGACY_QUERY_OPERATIONS == set(QueryName)
assert set(_COMMAND_MODELS) | LEGACY_COMMAND_OPERATIONS == set(CommandName)


def _operations() -> tuple[
    dict[QueryName, Operation[Any, Any, Any]],
    dict[CommandName, Operation[Any, Any, Any]],
]:
    queries = {
        name: (
            Operation(name, *_QUERY_MODELS[name])
            if name in _QUERY_MODELS
            else Operation(name, JsonObject, JsonObject, True)
        )
        for name in QueryName
    }
    commands = {
        name: (
            Operation(name, *_COMMAND_MODELS[name])
            if name in _COMMAND_MODELS
            else Operation(name, JsonObject, JsonObject, True)
        )
        for name in CommandName
    }
    return queries, commands


QUERY_OPERATIONS, COMMAND_OPERATIONS = _operations()


def query_operation(name: QueryName) -> Operation[QueryName, Any, Any]:
    return QUERY_OPERATIONS[name]


def command_operation(name: CommandName) -> Operation[CommandName, Any, Any]:
    return COMMAND_OPERATIONS[name]


__all__ = [
    "COMMAND_OPERATIONS",
    "LEGACY_COMMAND_OPERATIONS",
    "LEGACY_QUERY_OPERATIONS",
    "QUERY_OPERATIONS",
    "CommandName",
    "EmptyParams",
    "JsonObject",
    "Operation",
    "RosterGetParams",
    "TicketGetParams",
    "NamedReadParams",
    "QueryName",
    "command_operation",
    "query_operation",
]
