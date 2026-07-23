"""Codex app-server adapter: JSON frame evidence → observations + RPC lowering."""

# ruff: noqa: PLR0911, PLR0912, PLR0915

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from murder.llm.harness_control.adapters.base import HarnessActionAdapter, HarnessObservationAdapter
from murder.llm.harness_control.app_server.connection import AppServerConnection
from murder.llm.harness_control.model.actions import (
    AnswerPermission,
    AnswerQuestion,
    AppServerRpcEffect,
    ClearComposer,
    CommitPromptSubmission,
    ConfigureResumePicker,
    ConfigureSessionSettings,
    DismissOverlay,
    InsertPromptPayload,
    NavigateModelPicker,
    OpenModelPicker,
    OpenResumePicker,
    QuestionAnswerMode,
    RequestUsage,
    RestoreComposer,
    SelectModel,
    SemanticAction,
    SendInterrupt,
    SleepEffect,
    TerminalEffect,
)
from murder.llm.harness_control.model.evidence import (
    EvidenceDiagnostics,
    EvidenceEnvelope,
    EvidenceId,
    ScreenRegionRef,
    TerminalFrame,
)
from murder.llm.harness_control.model.observations import (
    ChoiceState,
    ComposerActionability,
    ComposerState,
    GenerationPhase,
    GenerationState,
    Knowledge,
    ModalKind,
    ModalState,
    ModelState,
    ObservationDelta,
    ObservationRevision,
    ObservationSnapshot,
    Observed,
    PermissionRequestState,
    QuestionState,
    SurfaceKind,
    SurfaceState,
    TranscriptTailState,
    TurnRef,
    UsageState,
    UsageWindow,
)

_EVIDENCE_TYPE = "codex.app_server.frame.v1"

_PERMISSION_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }
)
_QUESTION_METHODS = frozenset(
    {
        "mcpServer/elicitation/request",
        "tool/requestUserInput",
    }
)

_TURN_PHASE: dict[str, GenerationPhase] = {
    "idle": GenerationPhase.IDLE,
    "streaming": GenerationPhase.STREAMING,
    "completed": GenerationPhase.COMPLETE,
    "interrupted": GenerationPhase.STOPPED,
    "failed": GenerationPhase.STOPPED,
}

_ITEM_TYPE_ALIASES: dict[str, str] = {
    "agentmessage": "assistant",
    "assistant": "assistant",
    "assistantmessage": "assistant",
    "usermessage": "user",
    "user": "user",
    "reasoning": "assistant",
    "commandexecution": "tool_call",
    "filechange": "tool_call",
    "mcptoolcall": "tool_call",
    "tool_call": "tool_call",
    "toolcall": "tool_call",
}


def _fingerprint(text: str) -> str:
    """Match PromptDriver: sha256 of raw joined chunk text (no whitespace collapse)."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _present(
    value: object, ref: Any, at: datetime, rev: ObservationRevision
) -> Observed[object]:
    return Observed.present(value, evidence=(ref,), observed_at=at, revision=rev)


def _without(
    state: Knowledge,
    ref: Any,
    at: datetime,
    rev: ObservationRevision,
    *,
    explanation: str | None = None,
) -> Observed[object]:
    return Observed.without_value(
        state, evidence=(ref,), observed_at=at, revision=rev, explanation=explanation
    )


def _item_text(item: Mapping[str, Any]) -> str:
    for key in ("text", "content", "output", "command", "title"):
        value = item.get(key)
        if isinstance(value, str):
            return value
    return ""


def _segment_from_item(item: Mapping[str, Any]) -> dict[str, Any] | None:
    raw_type = str(item.get("type") or "").strip()
    role = _ITEM_TYPE_ALIASES.get(raw_type.replace(" ", "").casefold())
    if role is None:
        return None
    text = _item_text(item)
    if role == "user":
        return {"type": "user", "text": text}
    if role == "assistant":
        phase = "final"
        if isinstance(item.get("phase"), str) and item["phase"] in {"intermediate", "final"}:
            phase = item["phase"]
        return {
            "type": "assistant",
            "phase": phase,
            "text": text,
            "elapsed": item.get("elapsed") if isinstance(item.get("elapsed"), str) else None,
        }
    title = text or raw_type or "tool"
    return {
        "type": "tool_call",
        "title": title,
        "input": item.get("input") if isinstance(item.get("input"), str) else None,
        "result": item.get("result") if isinstance(item.get("result"), str) else None,
        "elided": bool(item.get("elided", False)),
        "running": bool(item.get("running", False)),
    }


def _transcript_state(
    turn_status: str | None, *, pending_requests: Sequence[object]
) -> str:
    if pending_requests:
        return "awaiting_approval"
    if turn_status == "streaming":
        return "working"
    if turn_status in {None, "idle", "completed", "interrupted", "failed"}:
        return "awaiting_input"
    return "working"


def _map_items(items: Sequence[object]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        segment = _segment_from_item(item)
        if segment is not None:
            segments.append(segment)
    return segments


def _decision_from_label(label: str | None) -> str:
    if not label:
        return "accept"
    folded = re.sub(r"[\s_-]+", "", label.casefold())
    if folded in {"acceptforsession", "session", "always"}:
        return "acceptForSession"
    if folded in {"decline", "deny", "reject", "no"}:
        return "decline"
    if folded in {"cancel", "abort"}:
        return "cancel"
    if folded in {"accept", "allow", "yes", "approve"}:
        return "accept"
    return label


def _pending_request_id(request: Mapping[str, Any]) -> str | int | None:
    raw = request.get("id")
    if isinstance(raw, (str, int)) and not isinstance(raw, bool):
        return raw
    return None


def _find_pending(
    pending: Sequence[object],
    *,
    methods: frozenset[str],
    hint: str | None,
) -> Mapping[str, Any] | None:
    candidates = [
        entry
        for entry in pending
        if isinstance(entry, Mapping) and str(entry.get("method") or "") in methods
    ]
    if not candidates:
        return None
    if hint is None:
        return candidates[0]
    hint_s = str(hint)
    for entry in candidates:
        request_id = _pending_request_id(entry)
        if request_id is not None and str(request_id) == hint_s:
            return entry
    return candidates[0]


def _usage_windows(usage: Mapping[str, Any]) -> tuple[UsageWindow, ...]:
    windows_raw = usage.get("windows")
    if not isinstance(windows_raw, list):
        return ()
    windows: list[UsageWindow] = []
    for row in windows_raw:
        if not isinstance(row, Mapping):
            continue
        name = row.get("name")
        if not isinstance(name, str) or not name:
            continue
        percent = row.get("percent_used")
        windows.append(
            UsageWindow(
                name,
                float(percent) if isinstance(percent, (int, float)) else None,
                None,
                str(row["reset_text"]) if isinstance(row.get("reset_text"), str) else None,
            )
        )
    return tuple(windows)


def _turn_status(turn: object) -> str | None:
    if isinstance(turn, dict) and isinstance(turn.get("status"), str):
        return str(turn["status"])
    return None


def _params_dict(request: Mapping[str, Any]) -> dict[str, Any]:
    raw = request.get("params")
    return raw if isinstance(raw, dict) else {}


class CodexAppServerHarnessAdapter(HarnessObservationAdapter, HarnessActionAdapter):
    """Project app-server frame JSON and lower actions to ``AppServerRpcEffect``."""

    parser_version = "codex-app-server-v1"

    def __init__(self, connection: AppServerConnection | None = None) -> None:
        # connection is required for lower() side effects on staged_composer_text
        self._connection = connection

    def parse_evidence(
        self, frame: TerminalFrame, history: Sequence[EvidenceEnvelope]
    ) -> Sequence[EvidenceEnvelope]:
        del history
        diagnostics: list[str] = []
        snapshot: dict[str, Any]
        try:
            decoded = json.loads(frame.raw_text)
            if not isinstance(decoded, dict):
                raise ValueError("frame JSON root must be an object")
            snapshot = decoded
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            diagnostics.append(f"app-server frame JSON invalid: {type(exc).__name__}: {exc}")
            snapshot = {
                "v": 1,
                "thread_id": None,
                "turn": None,
                "composer": {"text": "", "staged": False},
                "items": [],
                "pending_requests": [],
                "model": {"id": None, "effort": None},
                "usage": None,
            }

        turn = snapshot.get("turn") if isinstance(snapshot.get("turn"), dict) else None
        status = _turn_status(turn)
        composer_raw = (
            snapshot.get("composer") if isinstance(snapshot.get("composer"), dict) else {}
        )
        composer_text = (
            str(composer_raw.get("text", "")) if isinstance(composer_raw, dict) else ""
        )
        staged = bool(composer_raw.get("staged")) if isinstance(composer_raw, dict) else False
        items = snapshot.get("items") if isinstance(snapshot.get("items"), list) else []
        pending = (
            snapshot.get("pending_requests")
            if isinstance(snapshot.get("pending_requests"), list)
            else []
        )
        model = snapshot.get("model") if isinstance(snapshot.get("model"), dict) else {}
        usage = snapshot.get("usage") if isinstance(snapshot.get("usage"), dict) else None
        segments = _map_items(items)
        transcript = {
            "harness": "codex",
            "state": _transcript_state(status, pending_requests=pending),
            "segments": segments,
        }
        payload: dict[str, Any] = {
            "raw_frame": {
                "text": frame.raw_text,
                "ansi_preserved": frame.ansi_preserved,
                "width": frame.width,
                "height": frame.height,
                "pane_epoch": frame.pane_epoch,
                "capture_sequence": frame.capture_sequence,
            },
            "snapshot": snapshot,
            "thread_id": snapshot.get("thread_id"),
            "turn": turn,
            "composer": {
                "text": composer_text,
                "staged": staged,
                "normalized_text": composer_text,
                "fingerprint": _fingerprint(composer_text),
            },
            "activity": {
                "turn_status": status,
                "streaming": status == "streaming",
            },
            "model": {
                "id": model.get("id") if isinstance(model.get("id"), str) else None,
                "effort": model.get("effort") if isinstance(model.get("effort"), str) else None,
            },
            "pending_requests": list(pending),
            "usage": usage,
            "transcript": transcript,
        }
        return (
            EvidenceEnvelope(
                evidence_id=EvidenceId(f"codex-app-server:{frame.frame_id}:v1"),
                frame_id=frame.frame_id,
                harness_id=frame.harness_id,
                parser_version=self.parser_version,
                captured_at=frame.captured_at,
                evidence_type=_EVIDENCE_TYPE,
                payload=payload,
                source_regions=(ScreenRegionRef("app_server_snapshot"),),
                diagnostics=EvidenceDiagnostics(
                    parser_name=self.parser_version, messages=tuple(diagnostics)
                ),
            ),
        )

    def project_observations(
        self, evidence: Sequence[EvidenceEnvelope], prior: ObservationSnapshot | None
    ) -> ObservationDelta:
        item = next(
            (entry for entry in reversed(evidence) if entry.evidence_type == _EVIDENCE_TYPE),
            None,
        )
        if item is None:
            return ObservationDelta(
                updates={}, diagnostics=("no Codex app-server frame evidence",)
            )
        p = item.payload
        raw = p.get("raw_frame") if isinstance(p.get("raw_frame"), dict) else {}
        revision = ObservationRevision(
            int(raw.get("pane_epoch", 0)),
            int(raw.get("capture_sequence", 0)),
            (prior.revision.semantic_sequence + 1) if prior else 1,
        )
        ref, now = item.ref(), item.captured_at
        turn = p.get("turn") if isinstance(p.get("turn"), dict) else None
        status = _turn_status(turn)
        streaming = status == "streaming"
        idle = status in {None, "idle", "completed"}
        composer = p.get("composer") if isinstance(p.get("composer"), dict) else {}
        composer_text = str(composer.get("text", "")) if composer else ""
        staged = bool(composer.get("staged")) if composer else False
        pending = p.get("pending_requests") if isinstance(p.get("pending_requests"), list) else []
        permission_req = _find_pending(pending, methods=_PERMISSION_METHODS, hint=None)
        question_req = _find_pending(pending, methods=_QUESTION_METHODS, hint=None)

        if permission_req is not None:
            primary = SurfaceKind.PERMISSION_DIALOG
            modal_kind: ModalKind | None = ModalKind.PERMISSION
            modal_title: str | None = str(permission_req.get("method") or "permission")
        elif question_req is not None:
            primary = SurfaceKind.QUESTION_PICKER
            modal_kind = ModalKind.QUESTION
            modal_title = str(question_req.get("method") or "question")
        else:
            primary = SurfaceKind.COMPOSER
            modal_kind = None
            modal_title = None

        updates: dict[str, Observed[object]] = {
            "surface": _present(
                SurfaceState(
                    primary,
                    frozenset({primary, SurfaceKind.TRANSCRIPT}),
                    primary,
                    primary is not SurfaceKind.COMPOSER,
                    primary is not SurfaceKind.COMPOSER,
                ),
                ref,
                now,
                revision,
            ),
            "modal": (
                _present(
                    ModalState(modal_kind, modal_title, None, None, True, True),
                    ref,
                    now,
                    revision,
                )
                if modal_kind is not None
                else _without(Knowledge.ABSENT, ref, now, revision)
            ),
            "model_configuration": _without(
                Knowledge.UNSUPPORTED,
                ref,
                now,
                revision,
                explanation="app-server backend has no keystroke model picker",
            ),
            "settings": _without(
                Knowledge.UNSUPPORTED,
                ref,
                now,
                revision,
                explanation="app-server backend has no TUI session-settings chrome",
            ),
            "info": _without(Knowledge.ABSENT, ref, now, revision),
            "tool_activity": _without(Knowledge.ABSENT, ref, now, revision),
        }

        composer_open = primary is SurfaceKind.COMPOSER
        if composer_open or staged or composer_text:
            updates["composer"] = _present(
                ComposerState(
                    text=composer_text,
                    normalized_text=composer_text,
                    content_fingerprint=_fingerprint(composer_text),
                    cursor_visible=None,
                    focused=composer_open,
                    actionability=(
                        ComposerActionability.ACTIONABLE
                        if composer_open and idle
                        else ComposerActionability.VISIBLE_NOT_ACTIONABLE
                    ),
                    is_partial=False,
                    accepts_submission=composer_open and idle,
                ),
                ref,
                now,
                revision,
            )
        else:
            updates["composer"] = _without(
                Knowledge.UNKNOWN,
                ref,
                now,
                revision,
                explanation="composer occluded by pending request",
            )

        phase = _TURN_PHASE.get(status or "idle", GenerationPhase.UNKNOWN)
        updates["generation"] = _present(
            GenerationState(phase, streaming, None, None, None, None),
            ref,
            now,
            revision,
        )

        transcript = p.get("transcript") if isinstance(p.get("transcript"), dict) else {}
        segments_raw = transcript.get("segments")
        segments = segments_raw if isinstance(segments_raw, list) else []
        users = [s for s in segments if isinstance(s, dict) and s.get("type") == "user"]
        assistants = [
            s for s in segments if isinstance(s, dict) and s.get("type") == "assistant"
        ]
        user_text = str(users[-1].get("text", "")) if users else ""
        assistant_text = str(assistants[-1].get("text", "")) if assistants else ""
        latest_hash = None
        if assistant_text or user_text:
            latest_hash = _fingerprint(assistant_text or user_text)
        updates["transcript_tail"] = _present(
            TranscriptTailState(
                TurnRef(_fingerprint(f"user:{user_text}")[:16], "user") if users else None,
                TurnRef(_fingerprint(f"assistant:{assistant_text}")[:16], "assistant")
                if assistants
                else None,
                tuple(_fingerprint(str(s.get("text", ""))) for s in users[-8:]),
                streaming,
                bool(assistants) and not streaming and status == "completed",
                latest_hash,
                len(segments),
            ),
            ref,
            now,
            revision,
        )

        if permission_req is not None:
            request_id = _pending_request_id(permission_req)
            params = _params_dict(permission_req)
            updates["permission_request"] = _present(
                PermissionRequestState(
                    str(request_id) if request_id is not None else None,
                    str(params["tool"]) if isinstance(params.get("tool"), str) else None,
                    str(params["command"]) if isinstance(params.get("command"), str) else None,
                    str(params["description"])
                    if isinstance(params.get("description"), str)
                    else str(permission_req.get("method") or ""),
                    (
                        ChoiceState("accept", "accept"),
                        ChoiceState("decline", "decline"),
                        ChoiceState("acceptForSession", "acceptForSession"),
                        ChoiceState("cancel", "cancel"),
                    ),
                    None,
                    frozenset(),
                ),
                ref,
                now,
                revision,
            )
        else:
            updates["permission_request"] = _without(Knowledge.ABSENT, ref, now, revision)

        if question_req is not None:
            request_id = _pending_request_id(question_req)
            params = _params_dict(question_req)
            prompt = params.get("prompt") or params.get("question") or question_req.get("method")
            updates["question"] = _present(
                QuestionState(
                    str(request_id) if request_id is not None else None,
                    str(prompt) if prompt is not None else None,
                    (),
                    None,
                    None,
                    (),
                    True,
                    None,
                    None,
                    None,
                    (),
                ),
                ref,
                now,
                revision,
            )
        else:
            updates["question"] = _without(Knowledge.ABSENT, ref, now, revision)

        model = p.get("model") if isinstance(p.get("model"), dict) else {}
        model_id = model.get("id") if isinstance(model.get("id"), str) else None
        effort = model.get("effort") if isinstance(model.get("effort"), str) else None
        updates["active_model"] = (
            _present(ModelState(model_id, effort, model_id), ref, now, revision)
            if model_id
            else _without(
                Knowledge.UNKNOWN, ref, now, revision, explanation="no model in snapshot"
            )
        )

        usage = p.get("usage") if isinstance(p.get("usage"), dict) else None
        if usage is not None:
            windows = _usage_windows(usage)
            updates["usage"] = _present(
                UsageState(
                    str(usage["model"]) if isinstance(usage.get("model"), str) else model_id,
                    str(usage["plan"]) if isinstance(usage.get("plan"), str) else None,
                    windows,
                    "current",
                    SurfaceKind.COMPOSER,
                    None,
                    dict(usage),
                ),
                ref,
                now,
                revision,
            )
        else:
            updates["usage"] = _without(
                Knowledge.ABSENT, ref, now, revision, explanation="no usage in snapshot"
            )

        return ObservationDelta(
            updates=updates,
            evidence_refs=(ref,),
            diagnostics=item.diagnostics.messages,
        )

    def lower(
        self, action: SemanticAction, snapshot: ObservationSnapshot
    ) -> Sequence[TerminalEffect]:
        prefix = action.action_id or str(uuid4())
        connection = self._connection

        if isinstance(action, InsertPromptPayload):
            if connection is None:
                raise TypeError("InsertPromptPayload requires an AppServerConnection")
            connection.staged_composer_text = "".join(chunk.text for chunk in action.chunks)
            return (SleepEffect(f"{prefix}:stage", timedelta(0)),)

        if isinstance(action, ClearComposer):
            if connection is None:
                raise TypeError("ClearComposer requires an AppServerConnection")
            connection.staged_composer_text = ""
            return (SleepEffect(f"{prefix}:clear", timedelta(0)),)

        if isinstance(action, CommitPromptSubmission):
            if connection is None:
                raise TypeError("CommitPromptSubmission requires an AppServerConnection")
            staged = connection.staged_composer_text
            thread_id = connection.thread_id
            if not thread_id:
                raise ValueError("CommitPromptSubmission requires connection.thread_id")
            model_id: str | None = connection.desired_model
            effort: str | None = connection.desired_effort
            if (
                model_id is None
                and snapshot.active_model.knowledge is Knowledge.PRESENT
                and snapshot.active_model.value is not None
            ):
                model_id = snapshot.active_model.value.model_id
                effort = effort or snapshot.active_model.value.effort
            params: dict[str, object] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": staged}],
            }
            if model_id is not None:
                params["model"] = model_id
            if effort is not None:
                params["effort"] = effort
            connection.staged_composer_text = ""
            return (
                AppServerRpcEffect(
                    f"{prefix}:turn-start",
                    method="turn/start",
                    params=params,
                    expects_response=True,
                ),
            )

        if isinstance(action, SendInterrupt):
            if connection is None:
                raise TypeError("SendInterrupt requires an AppServerConnection")
            thread_id = connection.thread_id
            turn_id = connection.current_turn_id
            if not thread_id or not turn_id:
                raise ValueError(
                    "SendInterrupt requires connection.thread_id and connection.current_turn_id"
                )
            return (
                AppServerRpcEffect(
                    f"{prefix}:interrupt",
                    method="turn/interrupt",
                    params={"threadId": thread_id, "turnId": turn_id},
                    expects_response=True,
                ),
            )

        if isinstance(action, AnswerPermission):
            hint = action.request_id_hint or action.response_id
            request_id = _pending_from_snapshot_hint(snapshot, hint=hint) or _coerce_id(hint)
            if request_id is None:
                raise ValueError("AnswerPermission requires a pending request id")
            decision = _decision_from_label(action.response_label or action.response_id)
            return (
                AppServerRpcEffect(
                    f"{prefix}:permission",
                    method="",
                    expects_response=False,
                    response_id=request_id,
                    response_result={"decision": decision},
                ),
            )

        if isinstance(action, AnswerQuestion):
            hint = action.question_id_hint
            request_id = _pending_from_snapshot_hint(snapshot, hint=hint) or _coerce_id(hint)
            if request_id is None:
                raise ValueError("AnswerQuestion requires a pending request id")
            if action.mode is QuestionAnswerMode.DECLINE:
                result: dict[str, object] = {"decision": "decline"}
            elif action.custom_answer is not None:
                result = {"answers": [action.custom_answer]}
            elif action.selections:
                result = {"answers": [selection.label for selection in action.selections]}
            else:
                result = {"decision": "accept"}
            return (
                AppServerRpcEffect(
                    f"{prefix}:question",
                    method="",
                    expects_response=False,
                    response_id=request_id,
                    response_result=result,
                ),
            )

        if isinstance(action, SelectModel):
            if connection is None:
                raise TypeError("SelectModel requires an AppServerConnection")
            connection.desired_model = action.model_id
            connection.desired_effort = action.effort
            return (SleepEffect(f"{prefix}:stage-model", timedelta(0)),)

        if isinstance(action, RequestUsage):
            if snapshot.usage.knowledge is Knowledge.PRESENT and snapshot.usage.value is not None:
                return (SleepEffect(f"{prefix}:usage-present", timedelta(0)),)
            return (
                AppServerRpcEffect(
                    f"{prefix}:rate-limits",
                    method="account/rateLimits/read",
                    params={},
                    expects_response=True,
                ),
            )

        if isinstance(
            action,
            (
                OpenResumePicker,
                ConfigureResumePicker,
                OpenModelPicker,
                NavigateModelPicker,
                RestoreComposer,
                DismissOverlay,
                ConfigureSessionSettings,
            ),
        ):
            raise TypeError(
                f"Codex app-server backend does not support TUI-only action "
                f"{type(action).__name__}"
            )

        raise TypeError(
            f"Codex app-server lowering does not support {type(action).__name__}"
        )


def _coerce_id(value: str | int | None) -> str | int | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    return text or None


def _pending_from_snapshot_hint(
    snapshot: ObservationSnapshot, *, hint: str | None
) -> str | int | None:
    if hint is not None:
        return _coerce_id(hint)
    if (
        snapshot.permission_request.knowledge is Knowledge.PRESENT
        and snapshot.permission_request.value is not None
        and snapshot.permission_request.value.request_id_hint
    ):
        return _coerce_id(snapshot.permission_request.value.request_id_hint)
    if (
        snapshot.question.knowledge is Knowledge.PRESENT
        and snapshot.question.value is not None
        and snapshot.question.value.question_id_hint
    ):
        return _coerce_id(snapshot.question.value.question_id_hint)
    return None


__all__ = ["CodexAppServerHarnessAdapter"]
