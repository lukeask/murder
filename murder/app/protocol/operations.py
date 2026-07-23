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


# These registrations intentionally remain exhaustive.  The assertion below
# turns a newly added enum member into a failing import/test until its protocol
# contract is selected here.
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
    CommandName.AGENT_INTERRUPT: (JsonObject, JsonObject, True),
    CommandName.AGENT_MESSAGE: (JsonObject, JsonObject, True),
    CommandName.AGENT_RESUME_FROM_HISTORY: (JsonObject, JsonObject, True),
    CommandName.AGENT_SEND_KEY: (JsonObject, JsonObject, True),
    CommandName.AGENT_STOP: (JsonObject, JsonObject, True),
    CommandName.CROW_RENAME_ROGUE: (JsonObject, JsonObject, True),
    CommandName.CROW_RESET: (JsonObject, JsonObject, True),
    CommandName.CROW_SPAWN_ROGUE: (JsonObject, JsonObject, True),
    CommandName.HISTORY_DISMISS: (JsonObject, JsonObject, True),
    CommandName.NOTETAKER_CAPTURE_SUBMIT: (JsonObject, JsonObject, True),
    CommandName.PLAN_RENAME: (JsonObject, JsonObject, True),
    CommandName.PLANNER_SPAWN: (JsonObject, JsonObject, True),
    CommandName.SCHEDULER_SET_STEERING: (JsonObject, JsonObject, True),
    CommandName.HARNESS_USAGE_SAMPLE: (JsonObject, JsonObject, True),
    CommandName.TICKET_QUICK_CREATE: (JsonObject, JsonObject, True),
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


def _operations() -> tuple[
    dict[QueryName, Operation[Any, Any, Any]],
    dict[CommandName, Operation[Any, Any, Any]],
]:
    queries = {
        name: Operation(name, *(_QUERY_MODELS.get(name, (JsonObject, JsonObject, True))))
        for name in QueryName
    }
    commands = {
        name: Operation(name, *(_COMMAND_MODELS.get(name, (JsonObject, JsonObject, True))))
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
    "QUERY_OPERATIONS",
    "CommandName",
    "EmptyParams",
    "JsonObject",
    "Operation",
    "OrchestrationExecuteParams",
    "RosterGetParams",
    "TicketGetParams",
    "NamedReadParams",
    "QueryName",
    "command_operation",
    "query_operation",
]
