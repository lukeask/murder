"""Unit tests for AcpHarnessAdapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from murder.llm.harness_control.acp.agents import get_agent
from murder.llm.harness_control.acp.connection import AcpConnection
from murder.llm.harness_control.adapters.acp import AcpHarnessAdapter
from murder.llm.harness_control.adapters.cursor_acp import CursorAcpHarnessAdapter
from murder.llm.harness_control.model.actions import (
    AcpRpcEffect,
    AnswerPermission,
    CommitPromptSubmission,
    DuplicatePolicy,
    InputChunk,
    InputProvenance,
    InsertPromptPayload,
    OpenResumePicker,
    SendInterrupt,
    SleepEffect,
)
from murder.llm.harness_control.model.evidence import FrameId, HarnessId, TerminalFrame
from murder.llm.harness_control.model.observations import (
    ComposerActionability,
    GenerationPhase,
    Knowledge,
    SurfaceKind,
    unknown_snapshot,
)

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


def _frame(payload: dict[str, object], *, seq: int = 1) -> TerminalFrame:
    return TerminalFrame(
        FrameId(f"frame-{seq}"),
        HarnessId("cursor"),
        NOW,
        80,
        24,
        json.dumps(payload),
        False,
        0,
        seq,
    )


def _idle_staged(text: str = "hello world") -> dict[str, object]:
    return {
        "v": 1,
        "session_id": "sess-1",
        "turn": {"status": "idle"},
        "composer": {"text": text, "staged": True},
        "items": [],
        "pending_requests": [],
        "model": {"id": "gpt-5", "effort": "high"},
        "usage": None,
        "stop_reason": None,
    }


def _streaming() -> dict[str, object]:
    return {
        "v": 1,
        "session_id": "sess-1",
        "turn": {"status": "streaming"},
        "composer": {"text": "", "staged": False},
        "items": [
            {"id": "u1", "type": "userMessage", "text": "ping"},
            {"id": "a1", "type": "agentMessage", "text": "pong"},
        ],
        "pending_requests": [],
        "model": {"id": "gpt-5", "effort": None},
        "usage": None,
        "stop_reason": None,
    }


def _permission_pending() -> dict[str, object]:
    return {
        "v": 1,
        "session_id": "sess-1",
        "turn": {"status": "streaming"},
        "composer": {"text": "", "staged": False},
        "items": [],
        "pending_requests": [
            {
                "id": 42,
                "method": "session/request_permission",
                "params": {
                    "toolCall": {"title": "run ls", "kind": "execute", "rawInput": "ls"},
                },
            }
        ],
        "model": {"id": None, "effort": None},
        "usage": None,
        "stop_reason": None,
    }


def test_idle_staged_composer_projects_actionable_fingerprint() -> None:
    text = "hello  world\n"
    adapter = AcpHarnessAdapter()
    frame = _frame(_idle_staged(text))
    evidence = adapter.parse_evidence(frame, ())
    assert len(evidence) == 1
    assert evidence[0].evidence_type == "acp.frame.v1"
    assert evidence[0].payload["transcript"]["state"] == "awaiting_input"
    assert evidence[0].payload["composer"]["fingerprint"] == hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()

    delta = adapter.project_observations(
        evidence, unknown_snapshot(HarnessId("cursor"), captured_at=NOW)
    )
    composer = delta.updates["composer"]
    assert composer.knowledge is Knowledge.PRESENT
    assert composer.value is not None
    assert composer.value.text == text
    assert composer.value.content_fingerprint == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert composer.value.actionability is ComposerActionability.ACTIONABLE
    assert composer.value.accepts_submission is True
    assert delta.updates["generation"].value.phase is GenerationPhase.IDLE
    assert delta.updates["generation"].value.active is False


def test_streaming_turn_projects_working_generation() -> None:
    adapter = AcpHarnessAdapter()
    evidence = adapter.parse_evidence(_frame(_streaming()), ())
    assert evidence[0].payload["transcript"]["state"] == "working"
    segments = evidence[0].payload["transcript"]["segments"]
    assert segments[0]["type"] == "user"
    assert segments[1]["type"] == "assistant"

    delta = adapter.project_observations(
        evidence, unknown_snapshot(HarnessId("cursor"), captured_at=NOW)
    )
    generation = delta.updates["generation"]
    assert generation.value.phase is GenerationPhase.STREAMING
    assert generation.value.active is True
    tail = delta.updates["transcript_tail"]
    assert tail.value.assistant_streaming is True
    assert tail.value.transcript_revision == len(
        evidence[0].payload["transcript"]["segments"]
    )
    actionability = delta.updates["composer"].value.actionability
    assert actionability is ComposerActionability.VISIBLE_NOT_ACTIONABLE


def test_permission_request_projects_allow_once_choices() -> None:
    adapter = AcpHarnessAdapter(profile=get_agent("cursor"))
    evidence = adapter.parse_evidence(_frame(_permission_pending()), ())
    delta = adapter.project_observations(
        evidence, unknown_snapshot(HarnessId("cursor"), captured_at=NOW)
    )
    assert delta.updates["surface"].value.primary is SurfaceKind.PERMISSION_DIALOG
    permission = delta.updates["permission_request"]
    assert permission.knowledge is Knowledge.PRESENT
    assert permission.value is not None
    assert permission.value.request_id_hint == "42"
    labels = {choice.label for choice in permission.value.choices}
    assert labels == {"allow-once", "allow-always", "reject-once"}


def test_lower_insert_prompt_mutates_staged_text() -> None:
    connection = AcpConnection(transport=object())  # type: ignore[arg-type]
    adapter = AcpHarnessAdapter(connection)
    snapshot = unknown_snapshot(HarnessId("cursor"), captured_at=NOW)
    effects = adapter.lower(
        InsertPromptPayload(
            "insert-1",
            "op-1",
            DuplicatePolicy.SAFE_BEFORE_COMMIT,
            (
                InputChunk("hello ", InputProvenance.USER_TYPED, "c1"),
                InputChunk("world", InputProvenance.USER_PASTE_BLOCK, "c2"),
            ),
            hashlib.sha256(b"hello world").hexdigest(),
        ),
        snapshot,
    )
    assert connection.staged_composer_text == "hello world"
    assert len(effects) == 1
    assert isinstance(effects[0], SleepEffect)
    assert effects[0].duration.total_seconds() == 0


def test_lower_commit_yields_session_prompt() -> None:
    connection = AcpConnection(transport=object())  # type: ignore[arg-type]
    connection.session_id = "sess-1"
    connection.staged_composer_text = "ship it"
    adapter = AcpHarnessAdapter(connection)
    base = unknown_snapshot(HarnessId("cursor"), captured_at=NOW)
    effects = adapter.lower(
        CommitPromptSubmission("commit-1", "op-1", DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION),
        base,
    )
    assert len(effects) == 1
    effect = effects[0]
    assert isinstance(effect, AcpRpcEffect)
    assert effect.method == "session/prompt"
    assert effect.expects_response is True
    assert effect.params == {
        "sessionId": "sess-1",
        "prompt": [{"type": "text", "text": "ship it"}],
    }
    assert connection.staged_composer_text == ""


def test_lower_interrupt_yields_session_cancel() -> None:
    connection = AcpConnection(transport=object())  # type: ignore[arg-type]
    connection.session_id = "sess-1"
    adapter = AcpHarnessAdapter(connection)
    effects = adapter.lower(
        SendInterrupt("interrupt-1", "op-1", DuplicatePolicy.REPLAY_SAFE),
        unknown_snapshot(HarnessId("cursor"), captured_at=NOW),
    )
    assert len(effects) == 1
    effect = effects[0]
    assert isinstance(effect, AcpRpcEffect)
    assert effect.method == "session/cancel"
    assert effect.params == {"sessionId": "sess-1"}
    assert effect.expects_response is False


def test_lower_answer_permission_uses_selected_outcome() -> None:
    connection = AcpConnection(transport=object())  # type: ignore[arg-type]
    adapter = AcpHarnessAdapter(connection)
    request_id = 42
    effects = adapter.lower(
        AnswerPermission(
            "perm-1",
            "op-1",
            DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
            str(request_id),
            "allow-once",
            "allow-once",
        ),
        unknown_snapshot(HarnessId("cursor"), captured_at=NOW),
    )
    assert len(effects) == 1
    effect = effects[0]
    assert isinstance(effect, AcpRpcEffect)
    assert effect.response_id == request_id
    assert effect.response_result == {
        "outcome": {"outcome": "selected", "optionId": "allow-once"}
    }


def test_tui_only_actions_raise_type_error() -> None:
    adapter = AcpHarnessAdapter(AcpConnection(transport=object()))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="TUI-only"):
        adapter.lower(
            OpenResumePicker("resume-1", "op-1", DuplicatePolicy.REPLAY_SAFE),
            unknown_snapshot(HarnessId("cursor"), captured_at=NOW),
        )


def test_invalid_frame_json_emits_diagnostic() -> None:
    adapter = AcpHarnessAdapter()
    frame = replace(
        _frame(_idle_staged()),
        raw_text="not-json{",
    )
    evidence = adapter.parse_evidence(frame, ())
    assert evidence[0].diagnostics.messages
    assert "invalid" in evidence[0].diagnostics.messages[0]
    assert evidence[0].payload["transcript"]["segments"] == []


def test_cursor_acp_adapter_binds_cursor_profile() -> None:
    adapter = CursorAcpHarnessAdapter()
    assert adapter._profile is not None
    assert adapter._profile.agent_id == "cursor"
    assert "cursor/ask_question" in adapter._question_methods
