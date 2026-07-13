"""Claude Code adapter evidence, projection, and lowering contracts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import murder.llm.harness_control.adapters.claude_code as claude_code_module
from murder.llm.harness_control.adapters.claude_code import ClaudeCodeAdapter
from murder.llm.harness_control.model.actions import (
    AnswerPermission,
    AnswerQuestion,
    CommitPromptSubmission,
    DuplicatePolicy,
    InputChunk,
    InputProvenance,
    InsertPromptPayload,
    OpenModelPicker,
    QuestionAnswerMode,
    QuestionChoiceSelection,
    SelectModel,
)
from murder.llm.harness_control.model.evidence import TerminalFrame
from murder.llm.harness_control.model.observations import (
    Knowledge,
    ModelState,
    ObservationRevision,
    Observed,
    SurfaceKind,
    SurfaceState,
    unknown_snapshot,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"
NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)
EXPECTED_VISIBLE_RESUME_SESSIONS = 5


def _frame(name: str, *, transcript: bool = False) -> TerminalFrame:
    path = (
        FIXTURES / "transcripts" / "cc_mc_multiselect" / "frames" / name
        if transcript
        else FIXTURES / "harness_panes" / name
    )
    return TerminalFrame(
        frame_id=f"frame-{name}",
        harness_id="claude_code",
        captured_at=NOW,
        width=220,
        height=50,
        raw_text=path.read_text(),
        ansi_preserved=False,
        pane_epoch=0,
        capture_sequence=1,
    )


def _claude_transcript_frame(name: str, sequence: int) -> TerminalFrame:
    path = FIXTURES / "transcripts" / "cc" / "frames" / name
    return TerminalFrame(
        frame_id=f"claude-{name}",
        harness_id="claude_code",
        captured_at=NOW,
        width=93,
        height=50,
        raw_text=path.read_text(),
        ansi_preserved=False,
        pane_epoch=0,
        capture_sequence=sequence,
    )


def test_multiselect_evidence_retains_tabs_custom_answer_and_checked_state() -> None:
    adapter = ClaudeCodeAdapter()
    evidence = adapter.parse_evidence(_frame("0001.txt", transcript=True), [])
    payload = evidence[0].payload
    question = payload["question"]
    assert question["selection_mode"] == "multi"
    assert question["visible_tabs"] == ["Toppings", "Submit"]
    assert question["allow_custom_answer"] is True
    assert question["choices"][1]["label"] == "Mushroom"
    assert question["choices"][1]["checked"] is True
    assert evidence[0].payload["transcript"]["segments"]
    assert evidence[0].payload["raw_frame"]["capture_sequence"] == 1
    # The preceding answered single-select remains durable historical evidence
    # even while a different multi-select picker is currently live.
    assert evidence[0].payload["question_history"]["answered_summaries"] == [
        "User declined to answer questions"
    ]

    delta = adapter.project_observations(evidence, None)
    question_state = delta.updates["question"]
    assert question_state.knowledge is Knowledge.PRESENT
    assert question_state.value.active_tab == "Toppings"
    assert question_state.value.choices[1].highlighted is True
    assert {
        event["type"] for event in delta.semantic_events
    } >= {"claude_code.question_answered"}


def test_usage_context_and_subagent_data_remain_in_evidence_without_snapshot_bloat() -> None:
    adapter = ClaudeCodeAdapter()
    evidence = adapter.parse_evidence(_frame("cc_usage_dialog_wide.txt"), [])
    payload = evidence[0].payload
    composition = payload["context_composition"]
    assert composition["characteristics"] == [
        {
            "name": "subagent-heavy sessions",
            "percent_of_usage": 30.0,
            "period": "last_24h",
        }
    ]
    assert composition["contributors"] == [
        {"category": "subagent", "name": "Explore", "percent_of_usage": 7.0},
        {"category": "subagent", "name": "general-purpose", "percent_of_usage": 4.0},
        {"category": "mcp_server", "name": "tmuxmcp", "percent_of_usage": 1.0},
    ]
    assert any("does not include other devices" in line for line in composition["raw_lines"])
    assert payload["mcp_servers"] == [
        {
            "name": "tmuxmcp",
            "percent_of_usage": 1.0,
            "raw_line": "tmuxmcp                         1%",
        }
    ]
    assert payload["usage"]["session"]["input_tokens"] == 0

    delta = adapter.project_observations(evidence, None)
    assert delta.updates["usage"].knowledge is Knowledge.PRESENT
    assert "context_composition" not in delta.updates
    assert "mcp_servers" not in delta.updates


def test_agent_manager_sequence_retains_child_relationship_usage_and_activity() -> None:
    adapter = ClaudeCodeAdapter()
    dispatched = adapter.parse_evidence(_claude_transcript_frame("0094.txt", 94), [])
    managed = adapter.parse_evidence(_claude_transcript_frame("0105.txt", 105), dispatched)

    payload = managed[0].payload
    assert payload["agent_manager"] == {
        "visible": True,
        "selected_agent": "main",
        "main": {"name": "main", "selected": True, "relationship": "main"},
        "counts": {"active_children": 1, "recent_children": 0},
        "rows": [
            {
                "name": "main",
                "selected": True,
                "relationship": "main",
                "status": None,
                "task": None,
                "elapsed_seconds": None,
                "token_count": None,
                "raw_line": (
                    "● main                                                        "
                    "↑/↓ to select · Enter to view"
                ),
            },
            {
                "name": "general-purpose",
                "selected": False,
                "relationship": "child",
                "status": "active",
                "task": "Reconcile conflicted plan",
                "elapsed_seconds": 52,
                "token_count": 23100,
                "raw_line": (
                    "◯ general-purpose  Reconcile conflicted plan                           "
                    "52s · ↓ 23.1k tokens"
                ),
            },
        ],
    }
    assert payload["subagents"][-1] == {
        "name": "general-purpose",
        "role": "general-purpose",
        "task": "Reconcile conflicted plan",
        "status": "active",
        "activity": "Reconcile conflicted plan",
        "elapsed_seconds": 52,
        "token_count": 23100,
        "relationship": "child",
        "selected": False,
        "source": "agent_manager",
    }
    dispatched_delta = adapter.project_observations(dispatched, None)
    transitions = [
        event
        for event in dispatched_delta.semantic_events
        if event["type"] == "claude_code.subagent_active"
    ]
    assert len(transitions) == 1
    assert transitions[0]["previous_status"] == "dispatched"

    managed_delta = adapter.project_observations(managed, None)
    assert not any(
        event["type"] == "claude_code.subagent_active"
        for event in managed_delta.semantic_events
    )
    assert managed_delta.updates["tool_activity"].value.active == ()
    assert managed_delta.updates["tool_activity"].value.recent == ()


def test_permission_evidence_contains_choices_command_and_risk_attributes() -> None:
    raw = """Permission required to run command: `rm -rf build`

Which response?
❯ 1. Allow once
  2. Deny
Enter to select
"""
    frame = TerminalFrame("permission", "claude_code", NOW, 100, 30, raw, False, 0, 1)
    evidence = ClaudeCodeAdapter().parse_evidence(frame, [])
    permission = evidence[0].payload["permission"]
    assert permission["command"] == "rm -rf build"
    assert permission["choices"][0]["label"] == "Allow once"
    assert "shell" in permission["risk_attributes"]
    assert "write" in permission["risk_attributes"]

    trust_evidence = ClaudeCodeAdapter().parse_evidence(_frame("cc_trust_dialog.txt"), [])
    trust = trust_evidence[0].payload["trust_dialog"]
    assert trust["workspace"] == "/tmp/murder-cc-trust-recording-save-d6hNWt"
    assert trust["choices"][0]["label"] == "Yes, I trust this folder"
    assert trust["confirm_control"] == "Enter to confirm · Esc to cancel"
    trust_delta = ClaudeCodeAdapter().project_observations(trust_evidence, None)
    assert trust_delta.updates["surface"].value.primary is SurfaceKind.TRUST_DIALOG
    assert trust_delta.updates["question"].knowledge is Knowledge.ABSENT


def test_transcript_parser_failure_keeps_broad_evidence_durable(monkeypatch) -> None:
    def broken_parser(*_args, **_kwargs):
        raise ValueError("truncated frame")

    monkeypatch.setattr(claude_code_module, "parse_frames", broken_parser)
    evidence = ClaudeCodeAdapter().parse_evidence(_frame("cc_usage_dialog_weekly.txt"), [])

    assert evidence[0].payload["transcript"]["state"] == "unknown"
    assert "truncated frame" in evidence[0].diagnostics.messages[-1]
    assert evidence[0].payload["usage"]["windows"]


def test_resume_picker_retains_rows_while_projection_stays_narrow() -> None:
    adapter = ClaudeCodeAdapter()
    evidence = adapter.parse_evidence(_frame("cc_resume_picker.txt"), [])
    assert evidence[0].parser_version == "claude-code-evidence/v2"
    assert evidence[0].evidence_type == "claude_code.frame.v2"
    resume = evidence[0].payload["resume_picker"]

    assert resume["pagination"] == {"current": 1, "total": 50}
    assert resume["search"] == {"query": "", "placeholder": "Search…"}
    assert len(resume["sessions"]) == EXPECTED_VISIBLE_RESUME_SESSIONS
    assert resume["sessions"][0] == {
        "ordinal": 0,
        "title": "Investigate failing tests",
        "age": "in 0 sec.",
        "branch": "refactor/delete-crow-handler-queue",
        "size": "187.2KB",
        "project_path": "/home/user/Documents/code/murder",
        "highlighted": True,
        "scroll_marker": None,
        "disabled": None,
        "current": None,
        "default": None,
        "session_id": None,
    }
    assert resume["sessions"][4]["scroll_marker"] == "down"
    assert resume["controls"]["preview"] == "Space"

    delta = adapter.project_observations(evidence, None)
    assert delta.updates["surface"].value.primary is SurfaceKind.RESUME_PICKER
    modal = delta.updates["modal"].value
    assert modal.selected_index == 0
    assert modal.option_count == EXPECTED_VISIBLE_RESUME_SESSIONS
    assert modal.selected_index != resume["pagination"]["current"]
    assert modal.option_count != resume["pagination"]["total"]
    assert delta.semantic_events == (
        {
            "type": "claude_code.resume_picker_visible",
            "visible_count": 5,
            "pagination_current": 1,
            "pagination_total": 50,
            "empty": False,
        },
    )


def test_lowering_is_pure_and_uses_semantic_question_identity() -> None:
    adapter = ClaudeCodeAdapter()
    evidence = adapter.parse_evidence(_frame("0001.txt", transcript=True), [])
    delta = adapter.project_observations(evidence, None)
    revision = ObservationRevision(0, 1, 1)
    snapshot = unknown_snapshot("claude_code", captured_at=NOW, revision=revision)
    snapshot = replace(snapshot, question=delta.updates["question"])

    answer = AnswerQuestion(
        "answer",
        "op",
        DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
        None,
        QuestionAnswerMode.SINGLE,
        (QuestionChoiceSelection("number:4", "Pepper"),),
    )
    effects = adapter.lower(answer, snapshot)
    assert [effect.key for effect in effects] == ["Down", "Down", "Enter"]

    typed = InsertPromptPayload(
        "insert",
        "op",
        DuplicatePolicy.SAFE_BEFORE_COMMIT,
        (InputChunk("hello", InputProvenance.USER_TYPED, "typed"),),
        "fingerprint",
    )
    prompt_effects = adapter.lower(typed, snapshot)
    assert prompt_effects[0].text == "hello"
    assert (
        adapter.lower(
            CommitPromptSubmission("commit", "op", DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION),
            snapshot,
        )[0].key
        == "Enter"
    )
    composer_snapshot = replace(
        snapshot,
        surface=Observed.present(
            SurfaceState(
                SurfaceKind.COMPOSER,
                frozenset({SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}),
                SurfaceKind.COMPOSER,
                False,
                False,
            ),
            evidence=(),
            observed_at=NOW,
            revision=revision,
        ),
    )
    open_picker = adapter.lower(
        OpenModelPicker(
            "model-picker",
            "op",
            DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS,
        ),
        composer_snapshot,
    )
    assert open_picker[0].text == "/model"
    assert open_picker[1].key == "Enter"

    multi = AnswerQuestion(
        "multi",
        "op",
        DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
        None,
        QuestionAnswerMode.MULTIPLE,
        (
            QuestionChoiceSelection("number:1", "Chicken"),
            QuestionChoiceSelection("number:4", "Pepper"),
        ),
    )
    multi_effects = adapter.lower(multi, snapshot)
    assert [effect.key for effect in multi_effects] == [
        "Up",
        "Space",
        "Down",
        "Down",
        "Down",
        "Space",
        "Enter",
    ]


def test_permission_lowering_never_guesses_when_permission_not_observed() -> None:
    snapshot = unknown_snapshot("claude_code", captured_at=NOW)
    action = AnswerPermission(
        "perm", "op", DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY, None, "allow", "Allow once"
    )
    assert ClaudeCodeAdapter().lower(action, snapshot) == ()


def test_model_picker_retains_configuration_separately_from_active_readback() -> None:
    raw = (
        "▝▜█████▛▘  Sonnet 4.6 with medium effort · Claude Pro\n"
        + (FIXTURES / "harness_panes" / "cc_model_effort_high.txt").read_text()
    )
    frame = TerminalFrame("cc-model-picker", "claude_code", NOW, 220, 50, raw, False, 0, 1)
    adapter = ClaudeCodeAdapter()
    evidence = adapter.parse_evidence(frame, [])
    payload = evidence[0].payload["model"]
    config_evidence = payload["configuration"]

    assert payload["active"] == {
        "model_id": "sonnet",
        "effort": "medium",
        "display_name": "sonnet",
        "provider": "anthropic",
    }
    assert config_evidence["configured_model_id"] == "opus"
    assert config_evidence["highlighted_model_id"] == "opus"
    assert config_evidence["parameters"] == [("effort", "high")]
    assert config_evidence["stage"] == "model_picker"
    assert config_evidence["activation_control"]["use_current_session_key"] == "s"

    delta = adapter.project_observations(evidence, None)
    assert delta.updates["active_model"].value == ModelState(
        "sonnet", "medium", "sonnet", "anthropic"
    )
    assert delta.updates["model_configuration"].value.configured_model_id == "opus"
    assert dict(delta.updates["model_configuration"].value.parameters) == {"effort": "high"}


def test_model_lowering_distinguishes_configuration_and_activation() -> None:
    adapter = ClaudeCodeAdapter()
    evidence = adapter.parse_evidence(_frame("cc_model_effort_high.txt"), [])
    delta = adapter.project_observations(evidence, None)
    snapshot = replace(
        unknown_snapshot("claude_code", captured_at=NOW),
        model_configuration=delta.updates["model_configuration"],
    )

    configure = adapter.lower(
        SelectModel(
            "configure",
            "op",
            DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION,
            "haiku",
            effort="low",
        ),
        snapshot,
    )
    assert [effect.key for effect in configure] == ["Down", "Left", "Left", "Enter"]

    activate = adapter.lower(
        SelectModel(
            "activate",
            "op",
            DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION,
            "opus",
            effort="high",
        ),
        snapshot,
    )
    assert [effect.key for effect in activate] == ["s"]
