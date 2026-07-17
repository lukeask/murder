"""Allowlisted reconstruction of durable semantic operation state.

Persistence records type-marked JSON so a restart can recover a typed value,
but importing arbitrary type names from that JSON would turn the journal into
an object-construction interface.  This codec recognizes only operation-state
types owned by the verified harness-control architecture and rejects schema
drift explicitly.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum

from murder.llm.harness_control.capabilities.model_discovery import (
    DiscoverModelsOperation,
    DiscoverModelsRequest,
    ModelDiscoveryPhase,
)
from murder.llm.harness_control.capabilities.model_selection import (
    ModelSelectionPhase,
    ModelTarget,
    SelectModelOperation,
    SelectModelRequest,
)
from murder.llm.harness_control.capabilities.permissions import (
    AnswerPermissionOperation,
    AnswerPermissionPhase,
    PermissionAnswerRequest,
    PermissionDecisionKind,
    PermissionResponseTarget,
)
from murder.llm.harness_control.capabilities.questions import (
    AnswerQuestionOperation,
    AnswerQuestionPhase,
    QuestionAnswerRequest,
)
from murder.llm.harness_control.capabilities.restoration import (
    InterruptOperation,
    InterruptPhase,
    InterruptRequest,
    RestorationPhase,
    RestoreComposerOperation,
    RestoreComposerRequest,
)
from murder.llm.harness_control.capabilities.resume import (
    ConfigureResumeOperation,
    ConfigureResumePhase,
    OpenResumeOperation,
    OpenResumePhase,
    OpenResumeRequest,
    ResumePickerTarget,
)
from murder.llm.harness_control.capabilities.session_settings import (
    ConfigureSessionSettingsOperation,
    SessionSettingsPhase,
    SessionSettingsTarget,
)
from murder.llm.harness_control.capabilities.usage import UsageOperation, UsagePhase, UsageRequest
from murder.llm.harness_control.model.actions import (
    InputChunk,
    InputProvenance,
    QuestionAnswerMode,
    QuestionChoiceSelection,
)
from murder.llm.harness_control.model.observations import (
    ObservationRevision,
    SurfaceKind,
    TurnRef,
)
from murder.llm.harness_control.model.operations import (
    OperationEnvelope,
    OperationStatus,
    OperationWarning,
    PromptPayload,
    SubmitPhase,
    SubmitPromptOperation,
    SubmitPromptRequest,
)


class OperationDecodeError(ValueError):
    """Persisted state is unknown, malformed, or incompatible with this codec."""


def _type_name(value_type: type[object]) -> str:
    return f"{value_type.__module__}.{value_type.__qualname__}"


_DATACLASSES = (
    OperationEnvelope,
    OperationWarning,
    ObservationRevision,
    TurnRef,
    InputChunk,
    PromptPayload,
    SubmitPromptRequest,
    SubmitPromptOperation,
    ModelTarget,
    SelectModelRequest,
    SelectModelOperation,
    DiscoverModelsRequest,
    DiscoverModelsOperation,
    OpenResumeRequest,
    OpenResumeOperation,
    ResumePickerTarget,
    ConfigureResumeOperation,
    SessionSettingsTarget,
    ConfigureSessionSettingsOperation,
    QuestionChoiceSelection,
    QuestionAnswerRequest,
    AnswerQuestionOperation,
    PermissionResponseTarget,
    PermissionAnswerRequest,
    AnswerPermissionOperation,
    RestoreComposerRequest,
    RestoreComposerOperation,
    InterruptRequest,
    InterruptOperation,
    UsageRequest,
    UsageOperation,
)

_ENUMS = (
    OperationStatus,
    SubmitPhase,
    InputProvenance,
    ModelSelectionPhase,
    ModelDiscoveryPhase,
    OpenResumePhase,
    ConfigureResumePhase,
    SessionSettingsPhase,
    QuestionAnswerMode,
    AnswerQuestionPhase,
    PermissionDecisionKind,
    AnswerPermissionPhase,
    RestorationPhase,
    InterruptPhase,
    UsagePhase,
    SurfaceKind,
)

_TYPE_REGISTRY: dict[str, type[object]] = {
    _type_name(value_type): value_type for value_type in (*_DATACLASSES, *_ENUMS)
}

_OPERATION_TYPES = (
    SubmitPromptOperation,
    SelectModelOperation,
    DiscoverModelsOperation,
    OpenResumeOperation,
    ConfigureResumeOperation,
    ConfigureSessionSettingsOperation,
    AnswerQuestionOperation,
    AnswerPermissionOperation,
    RestoreComposerOperation,
    InterruptOperation,
    UsageOperation,
)


def decode_operation_value(  # noqa: PLR0911, PLR0912 -- each persisted marker is explicit
    value: object, *, path: str = "operation_state"
) -> object:
    """Decode one persisted value using the fixed semantic-state allowlist."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, list):
        return [decode_operation_value(item, path=f"{path}[]") for item in value]
    if not isinstance(value, dict):
        raise OperationDecodeError(f"{path}: unsupported persisted JSON value")

    marker = value.get("$type")
    if marker is None:
        return {
            str(key): decode_operation_value(item, path=f"{path}.{key}")
            for key, item in value.items()
        }
    if not isinstance(marker, str):
        raise OperationDecodeError(f"{path}: persisted type marker must be a string")
    if marker == "datetime":
        _require_keys(value, {"$type", "value"}, path)
        try:
            return datetime.fromisoformat(_require_string(value["value"], path))
        except ValueError as exc:
            raise OperationDecodeError(f"{path}: invalid persisted datetime") from exc
    if marker == "timedelta":
        _require_keys(value, {"$type", "seconds"}, path)
        seconds = value["seconds"]
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise OperationDecodeError(f"{path}: timedelta seconds must be numeric")
        return timedelta(seconds=seconds)
    if marker in {"tuple", "frozenset"}:
        _require_keys(value, {"$type", "items"}, path)
        items = value["items"]
        if not isinstance(items, list):
            raise OperationDecodeError(f"{path}: {marker} items must be a list")
        decoded = tuple(
            decode_operation_value(item, path=f"{path}[{index}]")
            for index, item in enumerate(items)
        )
        return decoded if marker == "tuple" else frozenset(decoded)

    value_type = _TYPE_REGISTRY.get(marker)
    if value_type is None:
        raise OperationDecodeError(f"{path}: unsupported persisted type {marker!r}")
    if issubclass(value_type, Enum):
        _require_keys(value, {"$type", "name"}, path)
        name = _require_string(value["name"], path)
        try:
            return value_type[name]  # type: ignore[index]
        except KeyError as exc:
            raise OperationDecodeError(
                f"{path}: unknown {value_type.__name__} member {name!r}"
            ) from exc

    _require_keys(value, {"$type", "fields"}, path)
    encoded_fields = value["fields"]
    if not isinstance(encoded_fields, dict):
        raise OperationDecodeError(f"{path}: dataclass fields must be an object")
    expected = {item.name for item in fields(value_type)}
    actual = set(encoded_fields)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise OperationDecodeError(
            f"{path}: schema mismatch for {value_type.__name__}; missing={missing}, extra={extra}"
        )
    decoded_fields = {
        name: decode_operation_value(item, path=f"{path}.{name}")
        for name, item in encoded_fields.items()
    }
    try:
        return value_type(**decoded_fields)
    except (TypeError, ValueError) as exc:
        raise OperationDecodeError(
            f"{path}: invalid state for {value_type.__name__}: {exc}"
        ) from exc


def decode_semantic_operation(value: object) -> object:
    """Reconstruct a supported capability operation, never an action/effect."""
    operation = decode_operation_value(value)
    if not isinstance(operation, _OPERATION_TYPES):
        type_name = type(operation).__name__
        raise OperationDecodeError(f"operation_state: unsupported operation root {type_name}")
    return operation


def _require_keys(value: dict[object, object], expected: set[str], path: str) -> None:
    actual = set(value)
    if actual != expected:
        raise OperationDecodeError(
            f"{path}: schema mismatch; missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _require_string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise OperationDecodeError(f"{path}: persisted value must be a string")
    return value


assert all(is_dataclass(value_type) for value_type in _DATACLASSES)

__all__ = ["OperationDecodeError", "decode_operation_value", "decode_semantic_operation"]
