"""Single source of truth for public application operations.

An operation binds one closed capability name to the Pydantic models accepted
and returned at the public boundary.  Transports, capability negotiation and
TypeScript generation must use this registry rather than independently
maintained name lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, JsonValue, RootModel

from murder.app.protocol.lifecycle import (
    AgentInterruptParams,
    AgentInterruptResult,
    AgentMessageParams,
    AgentMessageResult,
    AgentResumeFromHistoryParams,
    AgentResumeFromHistoryResult,
    AgentSendKeyParams,
    AgentSendKeyResult,
    AgentStopParams,
    AgentStopResult,
    CrowRenameRogueParams,
    CrowRenameRogueResult,
    CrowResetParams,
    CrowResetResult,
    CrowSpawnRogueParams,
    CrowSpawnRogueResult,
    HarnessAnswerParams,
    HarnessAnswerResult,
    HistoryDismissParams,
    HistoryDismissResult,
    ImageUploadParams,
    ImageUploadResult,
    NotetakerCaptureSubmitParams,
    NotetakerCaptureSubmitResult,
    PlanCreateParams,
    PlanCreateResult,
    PlanRenameParams,
    PlanRenameResult,
    PlannerSpawnParams,
    PlannerSpawnResult,
    TicketExistsParams,
    TicketExistsResult,
    TicketNextIdParams,
    TicketNextIdResult,
    TicketQuickCreateParams,
    TicketQuickCreateResult,
    TicketSaveBodyParams,
    TicketSaveBodyResult,
    TicketScheduleParams,
    TicketScheduleResult,
    TriggerFireParams,
    TriggerFireResult,
)
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
from murder.app.protocol.reads import (
    CommandGetParams,
    CommandGetResult,
    ConversationsGetResult,
    EmptyParams,
    HarnessModelsListResult,
    HealthGetResult,
    HistoryListResult,
    NamedReadParams,
    NoteGetResult,
    NotesListResult,
    PlanGetResult,
    PlansListResult,
    ReportGetResult,
    ReportsListResult,
    ScheduleGetResult,
    TicketGetParams,
    TicketGetResult,
    TransitGetResult,
    WorktreesListResult,
)
from murder.app.protocol.requests import CommandName, QueryName
from murder.app.protocol.session_control import (
    SampleHarnessUsageParams,
    SampleHarnessUsageResult,
    SetSchedulerSteeringParams,
    SetSchedulerSteeringResult,
)
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
from murder.app.protocol.settings import (
    ActivateLlmPolicyParams,
    CloneLlmPolicyParams,
    CloneLlmPolicyResult,
    CreateLlmPolicyParams,
    CreateLlmPolicyResult,
    CreateLlmProviderParams,
    CreateLlmProviderResult,
    DeleteLlmPolicyParams,
    DeleteLlmProviderParams,
    DiscoverLlmProviderModelsParams,
    DiscoverLlmProviderModelsResult,
    GetFavoritesParams,
    GetFavoritesResult,
    GetSettingsParams,
    GetSettingsResult,
    GetSpawnFavoritesParams,
    GetSpawnFavoritesResult,
    GetTemplatesParams,
    GetTemplatesResult,
    GetThemesParams,
    GetThemesResult,
    ImportThemeParams,
    ImportThemeResult,
    LlmMutationResult,
    PreviewLlmResolutionParams,
    PreviewLlmResolutionResult,
    SetFavoritesParams,
    SetFavoritesResult,
    SetLlmDisabledParams,
    SetLlmFeaturePolicyParams,
    SetSpawnFavoritesParams,
    SetSpawnFavoritesResult,
    SetTemplatesParams,
    SetTemplatesResult,
    SetThemesParams,
    SetThemesResult,
    UpdateLlmPolicyParams,
    UpdateLlmProviderModelsParams,
    UpdateLlmProviderParams,
    UpdateSettingsParams,
    UpdateSettingsResult,
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
    """Retained only so capability composition can still name an opaque object.

    Every closed query and command now binds a domain DTO; ``LEGACY_*`` is empty.
    Keep this adapter so tests and generators can still describe a JsonObject
    contract if a temporary compatibility endpoint is reintroduced.
    """


class RosterGetParams(EmptyParams):
    """``roster.get`` is deliberately argument-free.

    The roster implementation remains a read-model adapter for now; this DTO
    is the seam that lets its implementation move without widening the public
    request contract.
    """


@dataclass(frozen=True)
class Operation(Generic[Name, Params, Result]):
    name: Name
    params_model: object
    result_model: object
    legacy: bool = False


# Typed contracts.  A newly added enum member must land here with a named DTO
# or in the matching LEGACY_* set below; silent JsonObject fallback is forbidden.
_QUERY_MODELS: dict[QueryName, tuple[type[BaseModel], type[BaseModel], bool]] = {
    QueryName.HEALTH_GET: (EmptyParams, HealthGetResult, False),
    QueryName.COMMAND_GET: (CommandGetParams, CommandGetResult, False),
    QueryName.CONVERSATIONS_GET: (EmptyParams, ConversationsGetResult, False),
    QueryName.ROSTER_GET: (RosterGetParams, CrowSnapshot, False),
    QueryName.SCHEDULE_GET: (EmptyParams, ScheduleGetResult, False),
    QueryName.PLANS_LIST: (EmptyParams, PlansListResult, False),
    QueryName.NOTES_LIST: (EmptyParams, NotesListResult, False),
    QueryName.REPORTS_LIST: (EmptyParams, ReportsListResult, False),
    QueryName.HISTORY_LIST: (EmptyParams, HistoryListResult, False),
    QueryName.TRANSIT_GET: (EmptyParams, TransitGetResult, False),
    QueryName.TICKET_GET: (TicketGetParams, TicketGetResult, False),
    QueryName.PLAN_GET: (NamedReadParams, PlanGetResult, False),
    QueryName.NOTE_GET: (NamedReadParams, NoteGetResult, False),
    QueryName.REPORT_GET: (NamedReadParams, ReportGetResult, False),
    QueryName.HARNESS_MODELS_LIST: (EmptyParams, HarnessModelsListResult, False),
    QueryName.TICKET_NEXT_ID: (TicketNextIdParams, TicketNextIdResult, False),
    QueryName.TICKET_EXISTS: (TicketExistsParams, TicketExistsResult, False),
    QueryName.SETTINGS_GET: (GetSettingsParams, GetSettingsResult, False),
    QueryName.WORKTREES_LIST: (EmptyParams, WorktreesListResult, False),
    QueryName.FAVORITES_GET: (GetFavoritesParams, GetFavoritesResult, False),
    QueryName.SPAWN_FAVORITES_GET: (GetSpawnFavoritesParams, GetSpawnFavoritesResult, False),
    QueryName.TEMPLATES_GET: (GetTemplatesParams, GetTemplatesResult, False),
    QueryName.THEMES_GET: (GetThemesParams, GetThemesResult, False),
    QueryName.WORKFLOWS_GET: (GetWorkflowsParams, GetWorkflowsResult, False),
    QueryName.APPROVALS_LIST: (ListApprovalsParams, ListApprovalsResult, False),
    QueryName.APPROVALS_GET: (GetApprovalParams, GetApprovalResult, False),
    QueryName.PERMISSIONS_LIST: (ListPermissionsParams, ListPermissionsResult, False),
    QueryName.SESSION_WRITER_GET: (GetWriterLeaseParams, GetWriterLeaseResult, False),
    QueryName.WORKFLOW_RUNS_LIST: (ListWorkflowRunsParams, ListWorkflowRunsResult, False),
    QueryName.WORKFLOW_RUNS_GET: (GetWorkflowRunParams, GetWorkflowRunResult, False),
}
_COMMAND_MODELS: dict[CommandName, tuple[type[BaseModel], type[BaseModel], bool]] = {
    CommandName.HARNESS_ANSWER: (HarnessAnswerParams, HarnessAnswerResult, False),
    CommandName.IMAGE_UPLOAD: (ImageUploadParams, ImageUploadResult, False),
    CommandName.TICKET_SAVE_BODY: (TicketSaveBodyParams, TicketSaveBodyResult, False),
    CommandName.TICKET_SCHEDULE: (TicketScheduleParams, TicketScheduleResult, False),
    CommandName.PLAN_CREATE: (PlanCreateParams, PlanCreateResult, False),
    CommandName.SETTINGS_UPDATE: (UpdateSettingsParams, UpdateSettingsResult, False),
    CommandName.LLM_SETTINGS_SET_DISABLED: (SetLlmDisabledParams, LlmMutationResult, False),
    CommandName.LLM_PROVIDER_CREATE: (CreateLlmProviderParams, CreateLlmProviderResult, False),
    CommandName.LLM_PROVIDER_UPDATE: (UpdateLlmProviderParams, LlmMutationResult, False),
    CommandName.LLM_PROVIDER_DELETE: (DeleteLlmProviderParams, LlmMutationResult, False),
    CommandName.LLM_PROVIDER_MODELS_UPDATE: (
        UpdateLlmProviderModelsParams,
        LlmMutationResult,
        False,
    ),
    CommandName.LLM_PROVIDER_DISCOVER_MODELS: (
        DiscoverLlmProviderModelsParams,
        DiscoverLlmProviderModelsResult,
        False,
    ),
    CommandName.LLM_POLICY_CREATE: (CreateLlmPolicyParams, CreateLlmPolicyResult, False),
    CommandName.LLM_POLICY_UPDATE: (UpdateLlmPolicyParams, LlmMutationResult, False),
    CommandName.LLM_POLICY_DELETE: (DeleteLlmPolicyParams, LlmMutationResult, False),
    CommandName.LLM_POLICY_ACTIVATE: (ActivateLlmPolicyParams, LlmMutationResult, False),
    CommandName.LLM_POLICY_CLONE: (CloneLlmPolicyParams, CloneLlmPolicyResult, False),
    CommandName.LLM_FEATURE_POLICY_SET: (SetLlmFeaturePolicyParams, LlmMutationResult, False),
    CommandName.LLM_PREVIEW_RESOLUTION: (
        PreviewLlmResolutionParams,
        PreviewLlmResolutionResult,
        False,
    ),
    CommandName.FAVORITES_SET: (SetFavoritesParams, SetFavoritesResult, False),
    CommandName.SPAWN_FAVORITES_SET: (SetSpawnFavoritesParams, SetSpawnFavoritesResult, False),
    CommandName.TEMPLATES_SET: (SetTemplatesParams, SetTemplatesResult, False),
    CommandName.THEMES_SET: (SetThemesParams, SetThemesResult, False),
    CommandName.THEME_IMPORT: (ImportThemeParams, ImportThemeResult, False),
    CommandName.WORKFLOWS_SET: (SetWorkflowsParams, SetWorkflowsResult, False),
    CommandName.WORKFLOW_START: (StartWorkflowParams, StartWorkflowResult, False),
    CommandName.TRIGGER_FIRE: (TriggerFireParams, TriggerFireResult, False),
    CommandName.APPROVAL_DECIDE: (DecideApprovalParams, DecideApprovalResult, False),
    CommandName.SESSION_WRITER_ACQUIRE: (AcquireWriterLeaseParams, WriterLeaseResult, False),
    CommandName.SESSION_WRITER_RENEW: (RenewWriterLeaseParams, WriterLeaseResult, False),
    CommandName.SESSION_WRITER_RELEASE: (ReleaseWriterLeaseParams, WriterLeaseResult, False),
    CommandName.SESSION_COMMAND_EXECUTE: (
        ExecuteSessionCommandParams,
        ExecuteSessionCommandResult,
        False,
    ),
    CommandName.WORKFLOW_SIGNAL: (SignalWorkflowParams, SignalWorkflowResult, False),
    CommandName.AGENT_INTERRUPT: (AgentInterruptParams, AgentInterruptResult, False),
    CommandName.AGENT_MESSAGE: (AgentMessageParams, AgentMessageResult, False),
    CommandName.AGENT_RESUME_FROM_HISTORY: (
        AgentResumeFromHistoryParams,
        AgentResumeFromHistoryResult,
        False,
    ),
    CommandName.AGENT_SEND_KEY: (AgentSendKeyParams, AgentSendKeyResult, False),
    CommandName.AGENT_STOP: (AgentStopParams, AgentStopResult, False),
    CommandName.CROW_RENAME_ROGUE: (CrowRenameRogueParams, CrowRenameRogueResult, False),
    CommandName.CROW_RESET: (CrowResetParams, CrowResetResult, False),
    CommandName.CROW_SPAWN_ROGUE: (CrowSpawnRogueParams, CrowSpawnRogueResult, False),
    CommandName.HISTORY_DISMISS: (HistoryDismissParams, HistoryDismissResult, False),
    CommandName.NOTETAKER_CAPTURE_SUBMIT: (
        NotetakerCaptureSubmitParams,
        NotetakerCaptureSubmitResult,
        False,
    ),
    CommandName.PLAN_RENAME: (PlanRenameParams, PlanRenameResult, False),
    CommandName.PLANNER_SPAWN: (PlannerSpawnParams, PlannerSpawnResult, False),
    CommandName.SCHEDULER_SET_STEERING: (
        SetSchedulerSteeringParams,
        SetSchedulerSteeringResult,
        False,
    ),
    CommandName.HARNESS_USAGE_SAMPLE: (
        SampleHarnessUsageParams,
        SampleHarnessUsageResult,
        False,
    ),
    CommandName.TICKET_QUICK_CREATE: (TicketQuickCreateParams, TicketQuickCreateResult, False),
}

# Explicitly opaque operations.  Membership here is the only way an enum name
# may use JsonObject; missing coverage fails import via the assertions below.
LEGACY_QUERY_OPERATIONS: frozenset[QueryName] = frozenset()
LEGACY_COMMAND_OPERATIONS: frozenset[CommandName] = frozenset()

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
