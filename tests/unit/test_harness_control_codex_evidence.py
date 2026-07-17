from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from murder.llm.harness_control.adapters.codex import CodexHarnessAdapter
from murder.llm.harness_control.model.actions import (
    FAST_HUMANIZED_TYPING,
    AnswerQuestion,
    CommitPromptSubmission,
    ConfigureResumePicker,
    DismissOverlay,
    DuplicatePolicy,
    QuestionAnswerMode,
    QuestionChoiceSelection,
    SelectModel,
    SendLiteralKeys,
    SendNamedKey,
    SleepEffect,
)
from murder.llm.harness_control.model.evidence import FrameId, HarnessId, TerminalFrame
from murder.llm.harness_control.model.observations import (
    GenerationPhase,
    Knowledge,
    ModalKind,
    SurfaceKind,
    unknown_snapshot,
)

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
    assert resume_delta.updates["question"].knowledge is Knowledge.PRESENT
    assert resume_delta.updates["question"].value is not None
    assert len(resume_delta.updates["question"].value.choices) == 18  # noqa: PLR2004
    assert resume_delta.updates["question"].value.choices[0].highlighted is True

    reasoning = adapter.parse_evidence(_frame("codex_reasoning_low.txt"), (resume,))[0]
    assert reasoning.payload["modal"]["model_choices"] == []
    assert reasoning.payload["model"]["configuration"]["stage"] == "effort"
    assert reasoning.payload["model"]["configuration"]["configured_model_id"] == "gpt-5.5"

    for unsafe_name, expected_surface in (
        ("codex_update_menu.txt", "QUESTION_PICKER"),
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


def test_codex_live_unboxed_question_and_permission_surfaces_are_actionable() -> None:
    adapter = CodexHarnessAdapter()
    question_frame = replace(
        _frame("codex_idle.txt"),
        raw_text="""Question 1/1 (1 unanswered)

Pick a color
› 1. Red
  2. Green
  3. Blue
  4. None of the above  Optionally, add details in notes (tab).

option 1/4 | tab to add notes | enter to submit answer | esc to interrupt
""",
    )
    question_delta = adapter.project_observations(adapter.parse_evidence(question_frame, ()), None)
    question = question_delta.updates["question"]
    assert question.knowledge is Knowledge.PRESENT
    assert question.value is not None
    assert question.value.prompt_text == "Pick a color"
    assert tuple(choice.label for choice in question.value.choices) == (
        "Red",
        "Green",
        "Blue",
        "None of the above Optionally, add details in notes (tab).",
    )
    assert question.value.allow_custom_answer is False
    assert question.value.visible_tabs == ("notes",)

    base = unknown_snapshot(HarnessId("codex"), captured_at=question_frame.captured_at)
    question_snapshot = replace(
        base,
        surface=question_delta.updates["surface"],  # type: ignore[arg-type]
        question=question_delta.updates["question"],  # type: ignore[arg-type]
    )
    none_choice = question.value.choices[-1]
    noted = AnswerQuestion(
        "note-answer",
        "question-op",
        DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
        question.value.question_id_hint,
        QuestionAnswerMode.SINGLE,
        (QuestionChoiceSelection(none_choice.stable_choice_id, none_choice.label),),
        note="Purple",
    )
    effects = adapter.lower(noted, question_snapshot)
    assert effects[-3:] == (
        SendNamedKey("note-answer:open-notes", "Tab"),
        SendLiteralKeys("note-answer:note", "Purple", FAST_HUMANIZED_TYPING),
        SendNamedKey("note-answer:confirm", "Enter"),
    )

    permission_frame = replace(
        question_frame,
        raw_text="""Would you like to run the following command?

Environment: local
Reason: test denial
› 1. Yes, proceed (y)
  2. Yes, and don't ask again (p)
  3. No, and tell Codex what to do differently (esc)

Press enter to confirm or esc to cancel
""",
    )
    permission_delta = adapter.project_observations(
        adapter.parse_evidence(permission_frame, ()), None
    )
    permission = permission_delta.updates["permission_request"]
    assert permission.knowledge is Knowledge.PRESENT
    assert permission.value is not None
    assert permission.value.tool_name == "shell"
    assert permission.value.risk_attributes == frozenset({"shell"})


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


CODEX_PANE_FIXTURES = {
    "codex_startup.txt",
    "codex_idle.txt",
    "codex_idle_after_prose_narration.txt",
    "codex_busy.txt",
    "codex_interrupt.txt",
    "codex_model_list.txt",
    "codex_model_picker_gpt55.txt",
    "codex_reasoning_low.txt",
    "codex_reasoning_high.txt",
    "codex_reasoning_extrahi.txt",
    "codex_reasoning_medium.txt",
    "codex_usage_limit.txt",
    "codex_session_limit.txt",
    "codex_status_scrollback.txt",
    "codex_update_menu.txt",
    "codex_update_menu_dismissed.txt",
    "codex_update_menu_ptr2.txt",
    "codex_update_menu_ptr3.txt",
    "codex_resume_picker.txt",
    "codex_resume_invalid.txt",
    "codex_live_permission.txt",
    "codex_live_question.txt",
    "codex_hostile_numbered_prose.txt",
}


def _project(name: str):  # type: ignore[no-untyped-def]
    adapter, frame = CodexHarnessAdapter(), _frame(name)
    evidence = adapter.parse_evidence(frame, ())
    return evidence[0], adapter.project_observations(
        evidence, unknown_snapshot(HarnessId("codex"), captured_at=frame.captured_at)
    )


def test_codex_fixture_contract_inventory_is_exhaustive() -> None:
    actual = {
        path.name for path in (ROOT / "tests" / "fixtures" / "harness_panes").glob("codex_*.txt")
    }
    assert actual == CODEX_PANE_FIXTURES


def test_codex_startup_chrome_and_mcp_phase_are_structured() -> None:
    evidence, delta = _project("codex_startup.txt")

    assert evidence.payload["chrome"] == {
        "cli_version": "0.133.0",
        "directory": "~/Documents/code/murder",
        "tip": "Use /rename to rename your threads for easier thread resuming.",
        "rename_thread_tip": True,
    }
    assert delta.updates["generation"].value.phase is GenerationPhase.STARTING
    assert delta.updates["surface"].value.primary is SurfaceKind.COMPOSER
    assert delta.updates["active_model"].value.model_id == "gpt-5.4"
    assert delta.updates["info"].value.cli_version == "0.133.0"
    assert delta.updates["info"].value.directory == "~/Documents/code/murder"
    assert delta.updates["info"].value.rename_thread_tip is True


@pytest.mark.parametrize(
    ("fixture", "phase", "active"),
    (
        ("codex_idle.txt", GenerationPhase.IDLE, False),
        ("codex_idle_after_prose_narration.txt", GenerationPhase.IDLE, False),
        ("codex_busy.txt", GenerationPhase.THINKING, True),
        ("codex_interrupt.txt", GenerationPhase.STOPPED, False),
    ),
)
def test_codex_generation_contract(
    fixture: str, phase: GenerationPhase, active: bool
) -> None:
    _, delta = _project(fixture)
    generation = delta.updates["generation"].value
    assert generation.phase is phase
    assert generation.active is active


@pytest.mark.parametrize(
    ("fixture", "effort", "option"),
    (
        ("codex_reasoning_low.txt", "low", "1"),
        ("codex_reasoning_medium.txt", "medium", "2"),
        ("codex_reasoning_high.txt", "high", "3"),
        ("codex_reasoning_extrahi.txt", "xhigh", "4"),
    ),
)
def test_codex_reasoning_picker_contract(fixture: str, effort: str, option: str) -> None:
    evidence, delta = _project(fixture)
    configuration = delta.updates["model_configuration"].value

    assert evidence.payload["model"]["configuration"]["stage"] == "effort"
    assert configuration.configured_model_id == "gpt-5.5"
    assert dict(configuration.parameters)["effort"] == effort
    assert dict(configuration.parameters)[f"effort_option.{effort}"] == option
    assert delta.updates["surface"].value.primary is SurfaceKind.MODEL_PICKER


def test_codex_usage_limit_is_idle_but_observable() -> None:
    evidence, delta = _project("codex_usage_limit.txt")
    usage = delta.updates["usage"].value

    assert delta.updates["generation"].value.phase is GenerationPhase.IDLE
    assert usage.model == "gpt-5.4-mini"
    assert usage.advisory_text == evidence.payload["notices"][0]
    assert "You've hit your usage limit" in usage.advisory_text
    assert delta.semantic_events[-1]["type"] == "codex.usage_limit"


def test_codex_status_projects_account_plan_model_and_limits() -> None:
    _, delta = _project("codex_session_limit.txt")
    usage = delta.updates["usage"].value

    assert usage.model == "gpt-5.4"
    assert usage.plan == "Plus"
    assert tuple(window.name for window in usage.windows) == ("5h", "weekly")


@pytest.mark.parametrize(
    ("fixture", "selected"),
    (
        ("codex_update_menu.txt", "1"),
        ("codex_update_menu_ptr2.txt", "2"),
        ("codex_update_menu_ptr3.txt", "3"),
    ),
)
def test_codex_update_menu_pointer_variants_are_typed_questions(
    fixture: str, selected: str
) -> None:
    evidence, delta = _project(fixture)
    choices = evidence.payload["update_surface"]["choices"]

    assert next(row["id"] for row in choices if row["highlighted"]) == selected
    assert delta.updates["surface"].value.primary is SurfaceKind.QUESTION_PICKER
    assert delta.updates["question"].knowledge is Knowledge.PRESENT
    assert delta.updates["question"].value.choices[int(selected) - 1].highlighted is True
    assert delta.updates["permission_request"].knowledge is Knowledge.ABSENT


@pytest.mark.parametrize(
    ("fixture", "surface", "modal"),
    (
        ("codex_live_question.txt", SurfaceKind.QUESTION_PICKER, ModalKind.QUESTION),
        ("codex_live_permission.txt", SurfaceKind.PERMISSION_DIALOG, ModalKind.PERMISSION),
    ),
)
def test_codex_decision_dialogs_project_their_exact_surface(
    fixture: str, surface: SurfaceKind, modal: ModalKind
) -> None:
    _, delta = _project(fixture)

    assert delta.updates["surface"].value.primary is surface
    assert delta.updates["modal"].value.kind is modal
    assert delta.updates["modal"].value.selected_index == 0
    assert delta.updates["composer"].knowledge is Knowledge.UNKNOWN


def test_codex_update_selection_is_acknowledged_by_dismissed_historical_menu() -> None:
    adapter = CodexHarnessAdapter()
    initial = unknown_snapshot(
        HarnessId("codex"), captured_at=_frame("codex_update_menu_ptr2.txt").captured_at
    )
    update_frame = _frame("codex_update_menu_ptr2.txt")
    update_delta = adapter.project_observations(
        adapter.parse_evidence(update_frame, ()), initial
    )
    prior = replace(
        initial,
        surface=update_delta.updates["surface"],  # type: ignore[arg-type]
        composer=update_delta.updates["composer"],  # type: ignore[arg-type]
        modal=update_delta.updates["modal"],  # type: ignore[arg-type]
        question=update_delta.updates["question"],  # type: ignore[arg-type]
    )

    dismissed = _frame("codex_update_menu_dismissed.txt")
    dismissed_delta = adapter.project_observations(
        adapter.parse_evidence(dismissed, ()), prior
    )

    answered = dismissed_delta.updates["question"]
    assert answered.knowledge is Knowledge.PRESENT
    assert answered.value is not None
    assert answered.value.answered_summary == ("Skip",)


def test_codex_approved_permission_is_correlated_with_resulting_activity() -> None:
    adapter = CodexHarnessAdapter()
    permission_frame = _frame("codex_live_permission.txt")
    initial = unknown_snapshot(HarnessId("codex"), captured_at=permission_frame.captured_at)
    permission_delta = adapter.project_observations(
        adapter.parse_evidence(permission_frame, ()), initial
    )
    prior = replace(
        initial,
        permission_request=permission_delta.updates["permission_request"],  # type: ignore[arg-type]
    )
    progressed = replace(
        permission_frame,
        frame_id=FrameId("permission-approved"),
        capture_sequence=2,
        raw_text="""Would you like to run the following command?

$ printf codex-approval-ok
› 1. Yes, proceed (y)
  2. Yes, and don't ask again (p)
  3. No, and tell Codex what to do differently (esc)

Press enter to confirm or esc to cancel
✔ You approved codex to run printf codex-approval-ok this time
• Ran printf codex-approval-ok
  └ codex-approval-ok
• Working (4s • esc to interrupt)
""",
    )
    progressed_delta = adapter.project_observations(
        adapter.parse_evidence(progressed, ()), prior
    )

    acknowledged = progressed_delta.updates["permission_request"]
    assert acknowledged.knowledge is Knowledge.PRESENT
    assert acknowledged.value is not None
    assert acknowledged.value.acknowledged_response_id == "1"


def test_codex_resume_configuration_uses_live_probed_focus_order() -> None:
    adapter = CodexHarnessAdapter()
    frame = _frame("codex_resume_picker.txt")
    initial = unknown_snapshot(HarnessId("codex"), captured_at=frame.captured_at)
    delta = adapter.project_observations(adapter.parse_evidence(frame, ()), initial)
    snapshot = replace(
        initial,
        surface=delta.updates["surface"],  # type: ignore[arg-type]
        question=delta.updates["question"],  # type: ignore[arg-type]
    )
    action = ConfigureResumePicker(
        "configure",
        "resume-op",
        DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
        "needle",
        "all",
        "created",
    )

    assert adapter.lower(action, snapshot) == (
        SendNamedKey("configure:filter-all", "Right"),
        SleepEffect("configure:filter-settle", timedelta(milliseconds=300)),
        SendNamedKey("configure:focus-sort", "Tab"),
        SendNamedKey("configure:sort-created", "Right"),
        SleepEffect("configure:sort-settle", timedelta(milliseconds=300)),
        SendLiteralKeys("configure:search", "needle", FAST_HUMANIZED_TYPING),
        SleepEffect("configure:search-settle", timedelta(milliseconds=500)),
    )

    second = snapshot.question.value.choices[1]  # type: ignore[union-attr]
    resume = AnswerQuestion(
        "resume",
        "resume-op",
        DuplicatePolicy.NEVER_AUTOMATICALLY_REPLAY,
        snapshot.question.value.question_id_hint,  # type: ignore[union-attr]
        QuestionAnswerMode.SINGLE,
        (QuestionChoiceSelection(second.stable_choice_id, second.label),),
    )
    assert adapter.lower(resume, snapshot) == (
        SendNamedKey("resume:nav:0", "Down"),
        SleepEffect("resume:nav-settle:0", timedelta(milliseconds=150)),
        SendNamedKey("resume:confirm", "Enter"),
    )


def test_codex_resume_parser_reads_second_options_and_search_prefix() -> None:
    original = _frame("codex_resume_picker.txt")
    header = next(
        line
        for line in original.raw_text.splitlines()
        if "Filter:" in line and "Sort:" in line and not line.lstrip().startswith("#")
    )
    changed = replace(
        original,
        frame_id=FrameId("resume-configured"),
        raw_text=original.raw_text.replace(
            header,
            "Search: needle    Filter: Cwd [All]    Sort: Updated [Created]",
        ),
    )
    evidence = CodexHarnessAdapter().parse_evidence(changed, ())[0]
    resume = evidence.payload["resume_surface"]

    assert resume["search_text"] == "needle"
    assert resume["filter"] == {"selected": "All", "available": ["Cwd", "All"]}
    assert resume["sort"] == {
        "selected": "Created",
        "available": ["Updated", "Created"],
    }


def test_codex_resume_is_acknowledged_by_matching_resumed_transcript() -> None:
    adapter = CodexHarnessAdapter()
    picker_frame = _frame("codex_resume_picker.txt")
    initial = unknown_snapshot(HarnessId("codex"), captured_at=picker_frame.captured_at)
    picker_delta = adapter.project_observations(
        adapter.parse_evidence(picker_frame, ()), initial
    )
    prior = replace(
        initial,
        question=picker_delta.updates["question"],  # type: ignore[arg-type]
    )
    resumed_frame = _frame("codex_idle_after_prose_narration.txt")
    resumed_delta = adapter.project_observations(
        adapter.parse_evidence(resumed_frame, ()), prior
    )

    answered = resumed_delta.updates["question"]
    assert answered.knowledge is Knowledge.PRESENT
    assert answered.value is not None
    assert answered.value.answered_summary == (
        "Run the shell command 'echo Pharsalus' and then tell me the single word it "
        "printed. Do not edit any files.",
    )


def test_codex_closed_model_picker_is_not_revived_from_scrollback() -> None:
    original = _frame("codex_model_list.txt")
    closed = replace(
        original,
        frame_id=FrameId("model-picker-closed"),
        raw_text=(
            original.raw_text
            + "\n• Model changed to gpt-5.6-luna medium\n"
            + "  gpt-5.6-luna medium · ~/Documents/code/murder\n"
        ),
    )
    evidence, = CodexHarnessAdapter().parse_evidence(closed, ())

    assert evidence.payload["modal"]["kind"] is None
    assert evidence.payload["modal"]["model_choices"] == []
    assert evidence.payload["model"]["configuration"]["picker_visible"] is False


def test_codex_resume_dismissal_clears_search_then_closes_picker() -> None:
    original = _frame("codex_resume_picker.txt")
    header = next(
        line
        for line in original.raw_text.splitlines()
        if "Filter:" in line and "Sort:" in line and not line.lstrip().startswith("#")
    )
    searched = replace(
        original,
        frame_id=FrameId("resume-with-search"),
        raw_text=original.raw_text.replace(
            header,
            "Search: needle    Filter: Cwd [All]    Sort: Updated [Created]",
        ),
    )
    adapter = CodexHarnessAdapter()
    initial = unknown_snapshot(HarnessId("codex"), captured_at=searched.captured_at)
    delta = adapter.project_observations(adapter.parse_evidence(searched, ()), initial)
    snapshot = replace(
        initial,
        surface=delta.updates["surface"],  # type: ignore[arg-type]
        question=delta.updates["question"],  # type: ignore[arg-type]
    )
    action = DismissOverlay(
        "dismiss",
        "restore-op",
        DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS,
        "RESUME_PICKER",
    )

    assert adapter.lower(action, snapshot) == (
        SendNamedKey("dismiss:escape", "Escape"),
        SleepEffect("dismiss:await-search-clear", timedelta(milliseconds=1500)),
        SendNamedKey("dismiss:dismiss-after-clear", "Escape"),
    )


def test_codex_control_state_uses_authoritative_viewport_not_scrollback() -> None:
    history = _frame("codex_model_list.txt").raw_text + _frame(
        "codex_resume_picker.txt"
    ).raw_text
    idle = _frame("codex_idle.txt")
    frame = replace(
        idle,
        frame_id=FrameId("authoritative-viewport"),
        raw_text=history,
        viewport_text=idle.raw_text,
    )
    evidence, = CodexHarnessAdapter().parse_evidence(frame, ())
    delta = CodexHarnessAdapter().project_observations(
        (evidence,), unknown_snapshot(HarnessId("codex"), captured_at=frame.captured_at)
    )

    assert evidence.payload["modal"]["kind"] is None
    assert evidence.payload["resume_surface"]["present"] is False
    assert evidence.payload["question_surface"]["present"] is False
    assert delta.updates["surface"].value.primary is SurfaceKind.COMPOSER
    assert delta.updates["question"].knowledge is Knowledge.ABSENT
