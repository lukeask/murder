"""Typed agent/ticket/crow/plan/history/notetaker/trigger/harness/image contracts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, JsonValue, field_validator, model_validator

from murder.app.protocol.common import ApplicationModel

HarnessAnswerError = Literal[
    "invalid_decision_response",
    "decision_request_not_found",
    "decision_kind_mismatch",
    "request_identity_mismatch",
    "request_not_current",
    "response_already_recorded",
    "invalid_semantic_response",
    "execution_not_verified",
]

ImageUploadErrorCode = Literal["image_too_large", "invalid_base64", "empty_name"]


def _strip_nonempty(value: str, *, field: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError(f"{field} must be non-empty")
    return text


def _strip_optional(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


# --- ticket.next_id / ticket.exists -------------------------------------------------


class TicketNextIdParams(ApplicationModel):
    """Empty params object for ``ticket.next_id``."""


class TicketNextIdResult(ApplicationModel):
    ok: Literal[True] = True
    ticket_id: str


class TicketExistsParams(ApplicationModel):
    handle: str = Field(min_length=1)

    @field_validator("handle")
    @classmethod
    def strip_handle(cls, value: str) -> str:
        return _strip_nonempty(value, field="handle")


class TicketExistsResult(ApplicationModel):
    ok: Literal[True] = True
    exists: bool


# --- agent.* ------------------------------------------------------------------------


class AgentInterruptParams(ApplicationModel):
    agent_id: str = Field(min_length=1)

    @field_validator("agent_id")
    @classmethod
    def strip_agent_id(cls, value: str) -> str:
        return _strip_nonempty(value, field="agent_id")


class AgentInterruptResult(ApplicationModel):
    ok: bool | None = None
    handled: bool | None = None
    error: str | None = None


class AgentMessageParams(ApplicationModel):
    agent_id: str = Field(min_length=1)
    message: str
    ticket_id: str | None = None

    @field_validator("agent_id")
    @classmethod
    def strip_agent_id(cls, value: str) -> str:
        return _strip_nonempty(value, field="agent_id")

    @field_validator("ticket_id")
    @classmethod
    def strip_ticket_id(cls, value: str | None) -> str | None:
        return _strip_optional(value)


class AgentMessageResult(ApplicationModel):
    ok: bool | None = None
    handled: bool | None = None
    queued: bool | None = None
    error: str | None = None


class AgentResumeFromHistoryParams(ApplicationModel):
    conversation_id: str = Field(min_length=1)

    @field_validator("conversation_id")
    @classmethod
    def strip_conversation_id(cls, value: str) -> str:
        return _strip_nonempty(value, field="conversation_id")


class AgentResumeFromHistoryResult(ApplicationModel):
    ok: bool | None = None
    handled: bool | None = None
    agent_id: str | None = None
    resumed_from: str | None = None
    error: str | None = None


class AgentSendKeyParams(ApplicationModel):
    agent_id: str | None = None
    key: str = Field(min_length=1)
    literal: bool = False
    enter: bool = False
    log_user_input: str | None = None

    @field_validator("agent_id")
    @classmethod
    def strip_agent_id(cls, value: str | None) -> str | None:
        return _strip_optional(value)

    @field_validator("key")
    @classmethod
    def strip_key(cls, value: str) -> str:
        return _strip_nonempty(value, field="key")

    @field_validator("log_user_input")
    @classmethod
    def strip_log_user_input(cls, value: str | None) -> str | None:
        return _strip_optional(value)


class AgentSendKeyResult(ApplicationModel):
    ok: bool | None = None
    handled: bool | None = None
    error: str | None = None
    agent_id: str | None = None
    session: str | None = None
    key: str | None = None
    literal: bool | None = None
    enter: bool | None = None
    logged_user_input: bool | None = None
    operation_id: str | None = None
    action_id: str | None = None
    terminal_transport_accepted: bool | None = None
    harness_interpretation_verified: bool | None = None


class AgentStopParams(ApplicationModel):
    agent_id: str = Field(min_length=1)

    @field_validator("agent_id")
    @classmethod
    def strip_agent_id(cls, value: str) -> str:
        return _strip_nonempty(value, field="agent_id")


class AgentStopResult(ApplicationModel):
    ok: bool | None = None
    handled: bool | None = None
    agent_id: str | None = None
    error: str | None = None


# --- crow.* -------------------------------------------------------------------------


class CrowRenameRogueParams(ApplicationModel):
    agent_id: str = Field(min_length=1)
    name: str = Field(min_length=1)

    @field_validator("agent_id")
    @classmethod
    def strip_agent_id(cls, value: str) -> str:
        return _strip_nonempty(value, field="agent_id")

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return _strip_nonempty(value, field="name")


class CrowRenameRogueResult(ApplicationModel):
    ok: bool | None = None
    handled: bool | None = None
    agent_id: str | None = None
    old_agent_id: str | None = None
    error: str | None = None


class CrowResetParams(ApplicationModel):
    ticket_id: str = Field(min_length=1)

    @field_validator("ticket_id")
    @classmethod
    def strip_ticket_id(cls, value: str) -> str:
        return _strip_nonempty(value, field="ticket_id")


class CrowResetResult(ApplicationModel):
    ok: bool | None = None
    handled: bool | None = None
    error: str | None = None
    ticket_id: str | None = None
    prev_status: str | None = None


class CrowSpawnRogueParams(ApplicationModel):
    harness: str = Field(min_length=1)
    model: str
    effort: str | None = None
    name: str | None = None
    worktree_path: str | None = None
    worktree_branch: str | None = None

    @field_validator("harness")
    @classmethod
    def strip_harness(cls, value: str) -> str:
        return _strip_nonempty(value, field="harness")

    @field_validator("effort", "name", "worktree_path", "worktree_branch")
    @classmethod
    def strip_optional_fields(cls, value: str | None) -> str | None:
        return _strip_optional(value)


class CrowSpawnRogueResult(ApplicationModel):
    handled: bool
    agent_id: str
    ok: bool | None = None
    error: str | None = None


# --- history.dismiss ----------------------------------------------------------------


class HistoryDismissParams(ApplicationModel):
    item_id: str = Field(min_length=1)

    @field_validator("item_id")
    @classmethod
    def strip_item_id(cls, value: str) -> str:
        return _strip_nonempty(value, field="item_id")


class HistoryDismissResult(ApplicationModel):
    item_id: str
    status: Literal["dismissed"]


# --- notetaker.capture.submit -------------------------------------------------------


class NotetakerCaptureSubmitParams(ApplicationModel):
    raw: str | None = None
    text: str | None = None
    title: str | None = None

    @field_validator("raw", "text", "title")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return _strip_optional(value)

    @model_validator(mode="after")
    def require_raw_or_text(self) -> NotetakerCaptureSubmitParams:
        if not self.raw and not self.text:
            raise ValueError("raw or text is required")
        return self


class NotetakerCaptureSubmitResult(ApplicationModel):
    handled: bool = True
    name: str | None = None
    path: str | None = None
    error: str | None = None
    ok: bool | None = None
    note_name: str | None = None
    entry_id: int | None = None
    cleaned: str | None = None
    short_vers: str | None = None
    reply: str | None = None


# --- plan.* / planner.spawn ---------------------------------------------------------


class PlanRenameParams(ApplicationModel):
    old_name: str = Field(min_length=1)
    new_name: str = Field(min_length=1)

    @field_validator("old_name")
    @classmethod
    def strip_old_name(cls, value: str) -> str:
        return _strip_nonempty(value, field="old_name")

    @field_validator("new_name")
    @classmethod
    def strip_new_name(cls, value: str) -> str:
        return _strip_nonempty(value, field="new_name")


class PlanRenameResult(ApplicationModel):
    handled: bool
    old_name: str
    name: str
    materialized_path: str | None = None
    revision_count: int | None = None


class PlannerSpawnParams(ApplicationModel):
    plan_name: str = Field(min_length=1)
    harness: str = Field(min_length=1)
    model: str = ""
    effort: str | None = None

    @field_validator("plan_name")
    @classmethod
    def strip_plan_name(cls, value: str) -> str:
        return _strip_nonempty(value, field="plan_name")

    @field_validator("harness")
    @classmethod
    def strip_harness(cls, value: str) -> str:
        return _strip_nonempty(value, field="harness")

    @field_validator("effort")
    @classmethod
    def strip_effort(cls, value: str | None) -> str | None:
        return _strip_optional(value)


class PlannerSpawnResult(ApplicationModel):
    handled: bool
    agent_id: str


class PlanCreateParams(ApplicationModel):
    plan_name: str = ""
    auto_name: bool = False
    message: str = ""
    body: str | None = None

    @field_validator("plan_name")
    @classmethod
    def strip_plan_name(cls, value: str) -> str:
        return value.strip()


class PlanCreateResult(ApplicationModel):
    handled: bool | None = None
    ok: bool | None = None
    plan_name: str | None = None
    agent_id: str | None = None
    error: str | None = None


# --- ticket.* commands --------------------------------------------------------------


class TicketQuickCreateParams(ApplicationModel):
    title: str = Field(min_length=1)

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        return _strip_nonempty(value, field="title")


class TicketQuickCreateResult(ApplicationModel):
    handled: bool | None = None
    ok: bool | None = None
    ticket_id: str | None = None
    error: str | None = None
    title: str | None = None


class TicketSaveBodyParams(ApplicationModel):
    ticket_id: str = Field(min_length=1)
    body: str

    @field_validator("ticket_id")
    @classmethod
    def strip_ticket_id(cls, value: str) -> str:
        return _strip_nonempty(value, field="ticket_id")


class TicketSaveBodyResult(ApplicationModel):
    handled: bool | None = None
    ok: bool | None = None
    ticket_id: str | None = None
    error: str | None = None


class TicketScheduleParams(ApplicationModel):
    ticket_id: str = Field(min_length=1)
    duration: str = ""

    @field_validator("ticket_id")
    @classmethod
    def strip_ticket_id(cls, value: str) -> str:
        return _strip_nonempty(value, field="ticket_id")


class TicketScheduleResult(ApplicationModel):
    handled: bool | None = None
    ticket_id: str | None = None
    schedule_at: str | None = None
    ok: bool | None = None
    error: str | None = None


# --- trigger.fire -------------------------------------------------------------------


class TriggerFireParams(ApplicationModel):
    trigger_id: UUID
    occurrence_key: str | None = Field(default=None, min_length=1)

    @field_validator("occurrence_key")
    @classmethod
    def strip_occurrence_key(cls, value: str | None) -> str | None:
        return _strip_optional(value)


class TriggerFireResult(ApplicationModel):
    ok: Literal[True] = True
    trigger_id: str
    occurrence_key: str


# --- harness.answer -----------------------------------------------------------------


class HarnessAnswerParams(ApplicationModel):
    agent_id: str = Field(min_length=1)
    decision_request_id: str = Field(min_length=1)
    decision_kind: Literal["question", "permission"]
    request_identity: str = Field(min_length=1)
    decided_by: str = Field(min_length=1)
    response: dict[str, JsonValue]

    @field_validator("agent_id")
    @classmethod
    def strip_agent_id(cls, value: str) -> str:
        return _strip_nonempty(value, field="agent_id")

    @field_validator("decision_request_id")
    @classmethod
    def strip_decision_request_id(cls, value: str) -> str:
        return _strip_nonempty(value, field="decision_request_id")

    @field_validator("request_identity")
    @classmethod
    def strip_request_identity(cls, value: str) -> str:
        return _strip_nonempty(value, field="request_identity")

    @field_validator("decided_by")
    @classmethod
    def strip_decided_by(cls, value: str) -> str:
        return _strip_nonempty(value, field="decided_by")


class HarnessAnswerResult(ApplicationModel):
    ok: bool
    error: HarnessAnswerError | None = None


# --- image.upload -------------------------------------------------------------------


class ImageUploadParams(ApplicationModel):
    bytes: str = Field(min_length=1)
    name: str = Field(min_length=1)
    ext: str | None = "png"

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return _strip_nonempty(value, field="name")

    @field_validator("ext")
    @classmethod
    def strip_ext(cls, value: str | None) -> str | None:
        return _strip_optional(value)


class ImageUploadResult(ApplicationModel):
    ok: bool
    path: str | None = None
    error: str | None = None
    error_code: ImageUploadErrorCode | None = None


__all__ = [
    "AgentInterruptParams",
    "AgentInterruptResult",
    "AgentMessageParams",
    "AgentMessageResult",
    "AgentResumeFromHistoryParams",
    "AgentResumeFromHistoryResult",
    "AgentSendKeyParams",
    "AgentSendKeyResult",
    "AgentStopParams",
    "AgentStopResult",
    "CrowRenameRogueParams",
    "CrowRenameRogueResult",
    "CrowResetParams",
    "CrowResetResult",
    "CrowSpawnRogueParams",
    "CrowSpawnRogueResult",
    "HarnessAnswerError",
    "HarnessAnswerParams",
    "HarnessAnswerResult",
    "HistoryDismissParams",
    "HistoryDismissResult",
    "ImageUploadErrorCode",
    "ImageUploadParams",
    "ImageUploadResult",
    "NotetakerCaptureSubmitParams",
    "NotetakerCaptureSubmitResult",
    "PlanCreateParams",
    "PlanCreateResult",
    "PlanRenameParams",
    "PlanRenameResult",
    "PlannerSpawnParams",
    "PlannerSpawnResult",
    "TicketExistsParams",
    "TicketExistsResult",
    "TicketNextIdParams",
    "TicketNextIdResult",
    "TicketQuickCreateParams",
    "TicketQuickCreateResult",
    "TicketSaveBodyParams",
    "TicketSaveBodyResult",
    "TicketScheduleParams",
    "TicketScheduleResult",
    "TriggerFireParams",
    "TriggerFireResult",
]
