"""Unit tests for CodexAppServerHarnessAdapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from murder.llm.harness_control.adapters.codex_app_server import CodexAppServerHarnessAdapter
from murder.llm.harness_control.app_server.connection import AppServerConnection
from murder.llm.harness_control.model.actions import (
    AppServerRpcEffect,
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
    unknown_snapshot,
)

NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _frame(payload: dict[str, object], *, seq: int = 1) -> TerminalFrame:
    return TerminalFrame(
        FrameId(f"frame-{seq}"),
        HarnessId("codex"),
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
        "thread_id": "thread-1",
        "turn": {"id": "turn-1", "status": "idle"},
        "composer": {"text": text, "staged": True},
        "items": [],
        "pending_requests": [],
        "model": {"id": "gpt-5", "effort": "high"},
        "usage": None,
    }


def _streaming() -> dict[str, object]:
    return {
        "v": 1,
        "thread_id": "thread-1",
        "turn": {"id": "turn-2", "status": "streaming"},
        "composer": {"text": "", "staged": False},
        "items": [
            {"id": "u1", "type": "userMessage", "text": "ping"},
            {"id": "a1", "type": "agentMessage", "text": "pong"},
        ],
        "pending_requests": [],
        "model": {"id": "gpt-5", "effort": None},
        "usage": None,
    }


def test_idle_staged_composer_projects_actionable_fingerprint() -> None:
    text = "hello  world\n"
    adapter = CodexAppServerHarnessAdapter()
    frame = _frame(_idle_staged(text))
    evidence = adapter.parse_evidence(frame, ())
    assert len(evidence) == 1
    assert evidence[0].payload["transcript"]["state"] == "awaiting_input"
    assert evidence[0].payload["composer"]["fingerprint"] == hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()

    delta = adapter.project_observations(
        evidence, unknown_snapshot(HarnessId("codex"), captured_at=NOW)
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
    adapter = CodexAppServerHarnessAdapter()
    evidence = adapter.parse_evidence(_frame(_streaming()), ())
    assert evidence[0].payload["transcript"]["state"] == "working"
    segments = evidence[0].payload["transcript"]["segments"]
    assert segments[0]["type"] == "user"
    assert segments[1]["type"] == "assistant"

    delta = adapter.project_observations(
        evidence, unknown_snapshot(HarnessId("codex"), captured_at=NOW)
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


def test_lower_insert_prompt_mutates_staged_text() -> None:
    connection = AppServerConnection(transport=object())  # type: ignore[arg-type]
    adapter = CodexAppServerHarnessAdapter(connection)
    snapshot = unknown_snapshot(HarnessId("codex"), captured_at=NOW)
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


def test_lower_commit_yields_turn_start() -> None:
    connection = AppServerConnection(transport=object())  # type: ignore[arg-type]
    connection.thread_id = "thread-1"
    connection.staged_composer_text = "ship it"
    connection.desired_model = "gpt-5"
    connection.desired_effort = "high"
    adapter = CodexAppServerHarnessAdapter(connection)
    base = unknown_snapshot(HarnessId("codex"), captured_at=NOW)
    effects = adapter.lower(
        CommitPromptSubmission("commit-1", "op-1", DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION),
        base,
    )
    assert len(effects) == 1
    effect = effects[0]
    assert isinstance(effect, AppServerRpcEffect)
    assert effect.method == "turn/start"
    assert effect.expects_response is True
    assert effect.params == {
        "threadId": "thread-1",
        "input": [{"type": "text", "text": "ship it"}],
        "model": "gpt-5",
        "effort": "high",
    }
    assert connection.staged_composer_text == ""


def test_lower_interrupt_yields_turn_interrupt() -> None:
    connection = AppServerConnection(transport=object())  # type: ignore[arg-type]
    connection.thread_id = "thread-1"
    connection.current_turn_id = "turn-9"
    adapter = CodexAppServerHarnessAdapter(connection)
    effects = adapter.lower(
        SendInterrupt("interrupt-1", "op-1", DuplicatePolicy.REPLAY_SAFE),
        unknown_snapshot(HarnessId("codex"), captured_at=NOW),
    )
    assert len(effects) == 1
    effect = effects[0]
    assert isinstance(effect, AppServerRpcEffect)
    assert effect.method == "turn/interrupt"
    assert effect.params == {"threadId": "thread-1", "turnId": "turn-9"}
    assert effect.expects_response is True


def test_tui_only_actions_raise_type_error() -> None:
    adapter = CodexAppServerHarnessAdapter(AppServerConnection(transport=object()))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="TUI-only"):
        adapter.lower(
            OpenResumePicker("resume-1", "op-1", DuplicatePolicy.REPLAY_SAFE),
            unknown_snapshot(HarnessId("codex"), captured_at=NOW),
        )


def test_invalid_frame_json_emits_diagnostic() -> None:
    adapter = CodexAppServerHarnessAdapter()
    frame = replace(
        _frame(_idle_staged()),
        raw_text="not-json{",
    )
    evidence = adapter.parse_evidence(frame, ())
    assert evidence[0].diagnostics.messages
    assert "invalid" in evidence[0].diagnostics.messages[0]
    assert evidence[0].payload["transcript"]["segments"] == []
