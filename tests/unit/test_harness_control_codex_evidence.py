from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from murder.llm.harness_control.adapters.codex import CodexHarnessAdapter
from murder.llm.harness_control.model.actions import (
    FAST_HUMANIZED_TYPING,
    AnswerQuestion,
    CommitPromptSubmission,
    DuplicatePolicy,
    QuestionAnswerMode,
    QuestionChoiceSelection,
    SelectModel,
    SendLiteralKeys,
    SendNamedKey,
)
from murder.llm.harness_control.model.evidence import FrameId, HarnessId, TerminalFrame
from murder.llm.harness_control.model.observations import Knowledge, unknown_snapshot

ROOT = Path(__file__).parents[2]


def _frame(name: str) -> TerminalFrame:
    return TerminalFrame(
        FrameId(name),
        HarnessId("codex"),
        datetime(2026, 7, 11, tzinfo=timezone.utc),
        220,
        50,
        (ROOT / "tests" / "fixtures" / "harness_panes" / name).read_text(),
        False,
        0,
        1,
    )


def test_codex_evidence_retains_broad_status_and_unpromoted_menu_details(  # noqa: PLR0915
) -> None:
    adapter = CodexHarnessAdapter()
    evidence = adapter.parse_evidence(_frame("codex_model_picker_gpt55.txt"), ())[0]
    status_frame = _frame("codex_status_scrollback.txt")
    status = adapter.parse_evidence(status_frame, (evidence,))[0]

    assert evidence.parser_version == "codex-evidence-v5"
    assert evidence.evidence_type == "codex.frame.v4"
    assert evidence.payload["raw_frame"]["text"].startswith("# source:")
    assert evidence.payload["modal"]["model_choices"]
    assert evidence.payload["question_surface"]["present"] is False
    assert "recognized live" in evidence.payload["question_surface"]["reason"]

    fields = status.payload["status"]["fields"]
    expected = {
        "cli_version": "0.133.0",
        "directory": "~/Documents/code/murder",
        "permissions": "Custom (workspace, never)",
        "agents_md": "<none>",
        "account": "user@example.com",
        "plan": "Plus",
        "collaboration_mode": "Default",
        "session_id": "019e5c91-89a0-7ca1-9b8c-b407e537f7d6",
        "model_id": "gpt-5.4",
        "reasoning_effort": "high",
        "summary_mode": "auto",
    }
    assert {name: fields[name]["value"] for name in expected} == expected
    assert all(fields[name]["knowledge"] == "present" for name in expected)
    assert fields["workspace"]["knowledge"] == "absent"
    assert fields["workspace"]["value"] is None
    assert fields["account"]["raw_line"] == (
        "│  Account:              user@example.com (Plus)                                 │"
    )
    assert fields["account"]["line_number"] > 0
    assert status.payload["status"]["source"] == "slash:/status"
    assert status.payload["status"]["freshness"] == "current"
    assert status.payload["status"]["raw_lines"][-1].startswith("│  Weekly limit:")

    delta = adapter.project_observations(
        (status,), unknown_snapshot(HarnessId("codex"), captured_at=status_frame.captured_at)
    )
    assert "session_id" not in delta.updates
    assert "account" not in delta.updates

    resume_frame = _frame("codex_resume_picker.txt")
    resume = adapter.parse_evidence(resume_frame, (status,))[0]
    picker = resume.payload["resume_surface"]
    assert picker["present"] is True
    assert picker["title"] == "Resume a previous session"
    assert picker["search_text"] == ""
    assert picker["filter"] == {"selected": "Cwd", "available": ["Cwd", "All"]}
    assert picker["sort"] == {"selected": "Updated", "available": ["Updated", "Created"]}
    assert picker["pagination"] == {"selected_index": 1, "total_count": 18, "percent": 100}
    sessions = picker["sessions"]
    assert len(sessions) == picker["pagination"]["total_count"]
    assert sum(item["highlighted"] for item in sessions) == 1
    session_fields = {"age", "preview", "highlighted", "raw_line", "line_number"}
    assert all(set(item) == session_fields for item in sessions)
    assert sessions[0]["age"] == "9h ago"
    assert sessions[0]["preview"].startswith("Locate and read the file prize.txt")
    assert sessions[0]["highlighted"] is True
    assert picker["sessions"][0]["raw_line"].lstrip().startswith("❯ 9h ago")
    assert picker["sessions"][0]["line_number"] > 0
    assert picker["controls"]["resume"] == "enter"
    assert picker["controls"]["exit"] == ["esc", "ctrl+c"]
    resume_delta = adapter.project_observations(
        (resume,), unknown_snapshot(HarnessId("codex"), captured_at=resume_frame.captured_at)
    )
    assert resume_delta.updates["surface"].value is not None
    assert resume_delta.updates["surface"].value.primary.name == "RESUME_PICKER"
    assert resume_delta.updates["composer"].knowledge is Knowledge.UNKNOWN

    reasoning = adapter.parse_evidence(_frame("codex_reasoning_low.txt"), (resume,))[0]
    assert reasoning.payload["modal"]["model_choices"] == []
    assert reasoning.payload["model"]["configuration"]["stage"] == "effort"
    assert reasoning.payload["model"]["configuration"]["configured_model_id"] == "gpt-5.5"

    for unsafe_name, expected_surface in (
        ("codex_update_menu.txt", "UNKNOWN_OVERLAY"),
        ("codex_resume_invalid.txt", "SHELL"),
    ):
        unsafe_frame = _frame(unsafe_name)
        unsafe_evidence = adapter.parse_evidence(unsafe_frame, (reasoning,))[0]
        unsafe_delta = adapter.project_observations(
            (unsafe_evidence,),
            unknown_snapshot(HarnessId("codex"), captured_at=unsafe_frame.captured_at),
        )
        assert unsafe_delta.updates["surface"].value.primary.name == expected_surface
        assert unsafe_delta.updates["composer"].knowledge is Knowledge.UNKNOWN

    update = adapter.parse_evidence(_frame("codex_update_menu.txt"), (reasoning,))[0]
    assert update.payload["update_surface"]["current_version"] == "0.139.0"
    assert update.payload["update_surface"]["available_version"] == "0.141.0"
    assert len(update.payload["update_surface"]["choices"]) == 3  # noqa: PLR2004
    dismissed_frame = _frame("codex_update_menu_dismissed.txt")
    dismissed = adapter.parse_evidence(dismissed_frame, (update,))[0]
    assert dismissed.payload["update_surface"]["present"] is False
    assert dismissed.payload["update_surface"]["historical"] is True
    dismissed_delta = adapter.project_observations(
        (dismissed,),
        unknown_snapshot(HarnessId("codex"), captured_at=dismissed_frame.captured_at),
    )
    assert dismissed_delta.updates["surface"].value.primary.name == "COMPOSER"

    placeholder = adapter.parse_evidence(
        _frame("codex_idle_after_prose_narration.txt"), (dismissed,)
    )[0]
    assert placeholder.payload["composer"]["placeholder"].startswith("Use /skills")
    assert placeholder.payload["composer"]["text"] == ""


def test_codex_truncated_or_repeated_frame_is_retained_without_fabricating_control_state() -> None:
    adapter = CodexHarnessAdapter()
    original = _frame("codex_model_picker_gpt55.txt")
    truncated = replace(
        original,
        frame_id=FrameId("truncated"),
        raw_text=original.raw_text[:120],
        width=37,
        capture_sequence=2,
    )
    first = adapter.parse_evidence(truncated, ())[0]
    repeated = adapter.parse_evidence(truncated, (first,))[0]
    assert first.payload["raw_frame"]["text"] == truncated.raw_text
    assert first.payload["raw_frame"]["width"] == 37  # noqa: PLR2004 - fixture width
    assert repeated.payload["raw_frame"]["text"] == truncated.raw_text
    for sequence, raw in enumerate(
        ("", "line\nline\n", "\x1b[broken ANSI", original.raw_text.replace("\n", "\n\n")),
        start=3,
    ):
        malformed = replace(
            original,
            frame_id=FrameId(f"malformed-{sequence}"),
            raw_text=raw,
            width=19 + sequence,
            capture_sequence=sequence,
        )
        retained = adapter.parse_evidence(malformed, (first, repeated))[0]
        assert retained.payload["raw_frame"]["text"] == raw
        assert retained.payload["raw_frame"]["width"] == malformed.width


def test_codex_mcp_and_tool_evidence_preserves_provenance_without_inventing_file_writes() -> None:
    adapter = CodexHarnessAdapter()
    startup = adapter.parse_evidence(_frame("codex_startup.txt"), ())[0]
    tool_frame = TerminalFrame(
        FrameId("codex-tool-call"),
        HarnessId("codex"),
        datetime(2026, 7, 11, tzinfo=timezone.utc),
        220,
        50,
        (ROOT / "tests/fixtures/transcripts/codex/frames/0067.txt").read_text(),
        False,
        0,
        2,
    )
    tool_evidence = adapter.parse_evidence(tool_frame, (startup,))[0]
    tool_delta = adapter.project_observations(
        (tool_evidence,),
        unknown_snapshot(HarnessId("codex"), captured_at=tool_frame.captured_at),
    )

    assert startup.payload["activity"]["mcp_startup"] == {
        "raw_line": "• Starting MCP servers (0/2): codex_apps, tmux (0s • esc to interrupt)",
        "started_count": 0,
        "total_count": 2,
        "server_names": ["codex_apps", "tmux"],
        "elapsed_seconds": 0,
        "interruptible": True,
    }
    assert {
        "minutes": tool_evidence.payload["activity"]["busy"]["minutes"],
        "seconds": tool_evidence.payload["activity"]["busy"]["seconds"],
    } == {"minutes": 12, "seconds": 6}
    mcp_tool = next(
        row
        for row in tool_evidence.payload["activity"]["tools"]
        if row["tool_name"] == "tmux.create_session"
    )
    assert mcp_tool["status"] == "complete"
    assert mcp_tool["command"] == 'tmux.create_session({"name":"plan-sync-tests"})'
    assert mcp_tool["output"] == "Session 'plan-sync-tests' created on local."
    assert mcp_tool["raw"] == {
        "type": "tool_call",
        "title": 'tmux.create_session({"name":"plan-sync-tests"})',
        "input": None,
        "result": "Session 'plan-sync-tests' created on local.",
        "elided": False,
        "running": False,
    }
    projected = tool_delta.updates["tool_activity"].value
    assert projected is not None
    interaction = next(row for row in projected.recent if row.tool_name == "tmux.create_session")
    assert interaction.command == mcp_tool["command"]
    assert interaction.status == "complete"
    assert interaction.paths_read == ()
    assert interaction.paths_written == ()


def test_codex_projects_composer_and_active_model() -> None:
    adapter = CodexHarnessAdapter()
    frame = _frame("codex_idle.txt")
    evidence = adapter.parse_evidence(frame, ())
    delta = adapter.project_observations(
        evidence, unknown_snapshot(HarnessId("codex"), captured_at=frame.captured_at)
    )
    composer, active = delta.updates["composer"], delta.updates["active_model"]
    assert composer.knowledge is Knowledge.PRESENT
    assert composer.value is not None and composer.value.text == ""
    assert composer.value.cursor_visible is None
    assert composer.value.focused is None
    assert active.knowledge is Knowledge.PRESENT
    assert active.value is not None and active.value.model_id == "gpt-5.4-mini"


def test_codex_commit_lowering_is_only_enter() -> None:
    action = CommitPromptSubmission("commit", "op", DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION)
    effects = CodexHarnessAdapter().lower(action, None)  # type: ignore[arg-type]
    assert effects == (SendNamedKey("commit:commit", "Enter"),)
    assert action.duplicate_policy is DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION


def test_codex_numeric_question_answer_lowers_without_direct_terminal_io() -> None:
    action = AnswerQuestion(
        "answer",
        "op",
        DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
        "q",
        QuestionAnswerMode.SINGLE,
        (QuestionChoiceSelection("2", "No"),),
    )
    effects = CodexHarnessAdapter().lower(action, None)  # type: ignore[arg-type]
    assert effects == (SendLiteralKeys("answer:choice", "2", FAST_HUMANIZED_TYPING),)

    adapter = CodexHarnessAdapter()
    frame = _frame("codex_live_question.txt")
    projected = adapter.project_observations(
        adapter.parse_evidence(frame, ()),
        unknown_snapshot(HarnessId("codex"), captured_at=frame.captured_at),
    )
    assert projected.updates["question"].value is not None
    assert all(choice.disabled is False for choice in projected.updates["question"].value.choices)


def test_codex_recognizes_only_complete_local_decision_surfaces() -> None:
    adapter = CodexHarnessAdapter()

    question_frame = _frame("codex_live_question.txt")
    question = adapter.project_observations(
        adapter.parse_evidence(question_frame, ()),
        unknown_snapshot(HarnessId("codex"), captured_at=question_frame.captured_at),
    )
    assert question.updates["question"].value is not None
    assert question.updates["permission_request"].value is None

    permission_frame = _frame("codex_live_permission.txt")
    permission = adapter.project_observations(
        adapter.parse_evidence(permission_frame, ()),
        unknown_snapshot(HarnessId("codex"), captured_at=permission_frame.captured_at),
    )
    assert permission.updates["question"].value is None
    assert permission.updates["permission_request"].value is not None


def test_codex_hostile_numbered_prose_remains_evidence_not_an_actionable_surface() -> None:
    adapter = CodexHarnessAdapter()
    frame = _frame("codex_hostile_numbered_prose.txt")
    evidence = adapter.parse_evidence(frame, ())[0]
    projected = adapter.project_observations(
        (evidence,), unknown_snapshot(HarnessId("codex"), captured_at=frame.captured_at)
    )

    # The raw capture remains available for diagnostics, but transcript prose
    # cannot gain authority merely because it looks like a numbered decision.
    assert "1. Blue: deploy" in evidence.payload["raw_frame"]["text"]
    assert evidence.payload["question_surface"]["present"] is False
    assert evidence.payload["permission_surface"]["present"] is False
    assert projected.updates["question"].value is None
    assert projected.updates["permission_request"].value is None


def test_codex_model_picker_keeps_configuration_distinct_from_active_readback() -> None:
    adapter, frame = CodexHarnessAdapter(), _frame("codex_model_list.txt")
    delta = adapter.project_observations(
        adapter.parse_evidence(frame, ()),
        unknown_snapshot(HarnessId("codex"), captured_at=frame.captured_at),
    )
    configuration = delta.updates["model_configuration"]
    active = delta.updates["active_model"]

    assert configuration.knowledge is Knowledge.PRESENT
    assert configuration.value is not None
    assert configuration.value.configured_model_id == "gpt-5.4"
    assert configuration.value.highlighted_model_id == "gpt-5.4"
    assert configuration.value.parameters == (("effort", "high"),)
    assert active.knowledge is Knowledge.PRESENT
    assert active.value is not None and active.value.model_id == "gpt-5.4"
    assert delta.semantic_events == (
        {
            "type": "codex.model_picker_visible",
            "stage": "model",
            "configured_model_id": "gpt-5.4",
            "highlighted_model_id": "gpt-5.4",
        },
    )


def test_codex_model_lowering_uses_current_numeric_picker_identity() -> None:
    adapter, frame = CodexHarnessAdapter(), _frame("codex_model_list.txt")
    initial = unknown_snapshot(HarnessId("codex"), captured_at=frame.captured_at)
    delta = adapter.project_observations(adapter.parse_evidence(frame, ()), initial)
    snapshot = replace(
        initial,
        surface=delta.updates["surface"],  # type: ignore[arg-type]
        model_configuration=delta.updates["model_configuration"],  # type: ignore[arg-type]
    )
    action = SelectModel("select", "model-op", DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION, "gpt-5.5")

    assert adapter.lower(action, snapshot) == (
        SendLiteralKeys("select:select-model", "1", FAST_HUMANIZED_TYPING),
    )


def test_codex_model_lowering_requires_each_fresh_parameter_stage() -> None:
    adapter, frame = CodexHarnessAdapter(), _frame("codex_model_list.txt")
    initial = unknown_snapshot(HarnessId("codex"), captured_at=frame.captured_at)
    delta = adapter.project_observations(adapter.parse_evidence(frame, ()), initial)
    snapshot = replace(
        initial,
        surface=delta.updates["surface"],  # type: ignore[arg-type]
        model_configuration=delta.updates["model_configuration"],  # type: ignore[arg-type]
    )
    action = SelectModel(
        "select", "model-op", DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION, "gpt-5.5", effort="low"
    )

    assert adapter.lower(action, snapshot) == (
        SendLiteralKeys("select:select-model", "1", FAST_HUMANIZED_TYPING),
    )

    high_frame = _frame("codex_reasoning_high.txt")
    high_delta = adapter.project_observations(
        adapter.parse_evidence(high_frame, ()), snapshot
    )
    high_snapshot = replace(
        snapshot,
        surface=high_delta.updates["surface"],  # type: ignore[arg-type]
        model_configuration=high_delta.updates["model_configuration"],  # type: ignore[arg-type]
    )
    assert adapter.lower(action, high_snapshot) == (
        SendLiteralKeys("select:select-effort", "1", FAST_HUMANIZED_TYPING),
    )

    low_frame = _frame("codex_reasoning_low.txt")
    low_delta = adapter.project_observations(adapter.parse_evidence(low_frame, ()), high_snapshot)
    low_snapshot = replace(
        high_snapshot,
        surface=low_delta.updates["surface"],  # type: ignore[arg-type]
        model_configuration=low_delta.updates["model_configuration"],  # type: ignore[arg-type]
    )
    assert adapter.lower(action, low_snapshot) == (
        SendNamedKey("select:confirm-configuration", "Enter"),
    )
