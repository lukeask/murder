"""Unit tests for ClaudeAgentSdkHarnessAdapter."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from murder.llm.harness_control.adapters.claude_agent_sdk import ClaudeAgentSdkHarnessAdapter
from murder.llm.harness_control.agent_sdk.connection import AgentSdkConnection
from murder.llm.harness_control.model.actions import (
    AgentSdkEffect,
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
    Knowledge,
    ObservationSnapshot,
    unknown_snapshot,
)

NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


def _frame(payload: dict[str, object], *, seq: int = 1) -> TerminalFrame:
    return TerminalFrame(
        FrameId(f"frame-{seq}"),
        HarnessId("claude_code"),
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
        "model": {"id": "claude-sonnet-4", "effort": "high"},
        "usage": None,
    }


def test_idle_staged_composer_projects_actionable() -> None:
    connection = AgentSdkConnection(cwd="/tmp")
    adapter = ClaudeAgentSdkHarnessAdapter(connection)
    evidence = adapter.parse_evidence(_frame(_idle_staged()), ())
    delta = adapter.project_observations(evidence, None)
    assert delta.updates["composer"].knowledge is Knowledge.PRESENT
    assert delta.updates["composer"].value.text == "hello world"
    assert delta.updates["active_model"].value.model_id == "claude-sonnet-4"


def test_permission_pending_projects_dialog() -> None:
    connection = AgentSdkConnection(cwd="/tmp")
    adapter = ClaudeAgentSdkHarnessAdapter(connection)
    payload = _idle_staged("")
    payload["turn"] = {"status": "streaming"}
    payload["pending_requests"] = [
        {
            "id": "req-1",
            "method": "tool/can_use_tool",
            "params": {"tool": "Bash", "command": "ls", "description": "list"},
        }
    ]
    evidence = adapter.parse_evidence(_frame(payload), ())
    delta = adapter.project_observations(evidence, None)
    assert delta.updates["permission_request"].knowledge is Knowledge.PRESENT
    assert delta.updates["permission_request"].value.request_id_hint == "req-1"


def test_lower_prompt_interrupt_permission() -> None:
    connection = AgentSdkConnection(cwd="/tmp")
    adapter = ClaudeAgentSdkHarnessAdapter(connection)
    snapshot = unknown_snapshot(HarnessId("claude_code"), captured_at=NOW)
    policy = DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION

    stage = adapter.lower(
        InsertPromptPayload(
            "ins",
            "op-1",
            policy,
            (InputChunk("hello", InputProvenance.USER_PASTE_BLOCK, "c1"),),
            "fp",
        ),
        snapshot,
    )
    assert isinstance(stage[0], SleepEffect)
    assert connection.staged_composer_text == "hello"

    commit = adapter.lower(CommitPromptSubmission("go", "op-2", policy), snapshot)
    assert isinstance(commit[0], AgentSdkEffect)
    assert commit[0].op == "query"
    assert commit[0].params == {"prompt": "hello"}

    irq = adapter.lower(
        SendInterrupt("stop", "op-3", DuplicatePolicy.REPLAY_SAFE),
        snapshot,
    )
    assert isinstance(irq[0], AgentSdkEffect)
    assert irq[0].op == "interrupt"

    payload = _idle_staged("")
    payload["pending_requests"] = [
        {
            "id": "req-9",
            "method": "tool/can_use_tool",
            "params": {"tool": "Bash", "command": "ls"},
        }
    ]
    evidence = adapter.parse_evidence(_frame(payload), ())
    delta = adapter.project_observations(evidence, None)
    base = unknown_snapshot(HarnessId("claude_code"), captured_at=NOW)
    fields = {
        key: value
        for key, value in delta.updates.items()
        if key in ObservationSnapshot.__dataclass_fields__
    }
    snap = replace(base, **fields)
    effects = adapter.lower(
        AnswerPermission("p", "op-4", policy, "req-9", "accept", "accept"),
        snap,
    )
    assert isinstance(effects[0], AgentSdkEffect)
    assert effects[0].op == "respond_permission"
    assert effects[0].request_id == "req-9"
    assert effects[0].permission_behavior == "allow"


def test_tui_only_actions_rejected() -> None:
    adapter = ClaudeAgentSdkHarnessAdapter(AgentSdkConnection(cwd="/tmp"))
    snapshot = unknown_snapshot(HarnessId("claude_code"), captured_at=NOW)
    with pytest.raises(TypeError, match="TUI-only"):
        adapter.lower(
            OpenResumePicker("resume", "op-1", DuplicatePolicy.REPLAY_SAFE),
            snapshot,
        )
