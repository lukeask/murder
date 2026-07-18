"""Fixture-backed coverage for Cursor's verified-architecture edge adapter."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from murder.llm.harness_control.adapters.cursor import CursorHarnessAdapter
from murder.llm.harness_control.model import (
    ClearComposer,
    DismissOverlay,
    DuplicatePolicy,
    HarnessId,
    InputChunk,
    InputProvenance,
    InsertPromptPayload,
    Knowledge,
    NavigateModelPicker,
    Observed,
    PasteBuffer,
    SelectModel,
    SendLiteralKeys,
    SendNamedKey,
    SurfaceKind,
    SurfaceState,
    TerminalFrame,
    unknown_snapshot,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "harness_panes"
NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)
TERMINAL_CONTEXT_PERCENT = 7.4
HTTP_USAGE_PERCENT = 12.5
USAGE_EVIDENCE_COUNT = 2
PICKER_VISIBLE_CHOICES = 10
SHORT_PROMPT = (
    "Write a detailed 2000-word history of timekeeping devices, century by century, thinking "
    "step by step. Do not use any tools."
)
WRAPPED_PROMPT = (
    "Build a small dependency-free Python project named tinyboard in this checkout. It should "
    "be a CLI task board storing tasks in JSON, with add, list, complete, and remove commands. "
    "Keep domain logic separate from argparse, use atomic file replacement, include useful "
    "validation and deterministic output, add pytest tests, a pyproject.toml, and a concise "
    "README. Implement it fully, run the tests, and keep the design simple."
)


def _frame(name: str, *, sequence: int = 1) -> TerminalFrame:
    return TerminalFrame(
        f"frame-{name}",
        HarnessId("cursor"),
        NOW,
        220,
        50,
        (FIXTURES / name).read_text(),
        name == "cursor_idle_input_filled.txt",
        0,
        sequence,
    )


def _text_frame(text: str) -> TerminalFrame:
    return TerminalFrame(
        "frame-inline",
        HarnessId("cursor"),
        NOW,
        120,
        30,
        text,
        False,
        0,
        1,
    )


def test_completed_prose_about_running_workers_does_not_block_composer() -> None:
    adapter = CursorHarnessAdapter()
    frame = _text_frame(
        "Created the model refresh for running workers.\n\n"
        " → Add a follow-up\n\n"
        " Composer 2.5 · 14.2%                         Run Everything\n"
        " ~/Documents/code/murder · main\n"
    )

    evidence = adapter.parse_evidence(frame, ())
    snapshot = adapter.project_observations(evidence, prior=None).updates

    assert snapshot["generation"].value is not None
    assert snapshot["generation"].value.active is False
    assert snapshot["composer"].value is not None
    assert snapshot["composer"].value.accepts_submission is True


def test_composer_and_attachment_evidence_is_retained_without_widening_snapshot() -> None:
    adapter = CursorHarnessAdapter()
    evidence = adapter.parse_evidence(_frame("cursor_idle_input_filled.txt"), ())
    payload = evidence[0].payload

    assert evidence[0].parser_version == "cursor-evidence-v3"
    assert evidence[0].evidence_type == "cursor.frame.v3"
    assert payload["raw_frame"]["ansi_preserved"] is True
    assert payload["composer"]["text"] == SHORT_PROMPT
    assert payload["composer"]["fingerprint"] == hashlib.sha256(SHORT_PROMPT.encode()).hexdigest()
    assert payload["composer"]["queued_follow_up"] is None
    # Attachment data remains harness evidence even though it is not a shared
    # controller field beyond ComposerState.attachments.
    assert "attachments" in payload["composer"]
    assert payload["composer"]["attachments"] == ()


def test_cursor_july_wrapped_composer_retains_exact_payload_identity() -> None:
    payload = CursorHarnessAdapter().parse_evidence(
        _frame("cursor_july_wrapped_composer.txt"), ()
    )[0].payload["composer"]

    assert payload["text"] == WRAPPED_PROMPT
    assert payload["normalized_text"] == WRAPPED_PROMPT
    assert payload["fingerprint"] == hashlib.sha256(WRAPPED_PROMPT.encode()).hexdigest()


def test_cursor_composer_does_not_infer_missing_wrapped_rows() -> None:
    frame = _frame("cursor_july_wrapped_composer.txt")
    first_row_only = frame.raw_text.replace(
        "   argparse, use atomic file replacement, include useful validation and deterministic "
        "output, add pytest tests, a pyproject.toml, and a concise README. Implement it fully, "
        "run the tests, and keep the design simple.",
        "",
    )
    payload = CursorHarnessAdapter().parse_evidence(
        replace(frame, raw_text=first_row_only), ()
    )[0].payload["composer"]

    assert payload["fingerprint"] != hashlib.sha256(WRAPPED_PROMPT.encode()).hexdigest()


def test_picker_parameters_and_active_readback_are_separate_evidence() -> None:
    adapter = CursorHarnessAdapter()
    picker_evidence = adapter.parse_evidence(_frame("cursor_model_list_fast_active.txt"), ())
    picker = picker_evidence[0].payload
    hovered_params = adapter.parse_evidence(_frame("cursor_opus_effort_low_hover.txt"), ())[
        0
    ].payload
    current_params = adapter.parse_evidence(_frame("cursor_opus_effort_medium_selected.txt"), ())[
        0
    ].payload
    status = adapter.parse_evidence(_frame("cursor_status_fast_active.txt"), ())[0].payload

    assert picker["models"]["picker"]["visible"] is True
    assert picker["models"]["picker"]["page"] == (1, 10, 27)
    pointed = next(
        row for row in picker["models"]["picker"]["choices"] if row["model_id"] == "composer-2.5"
    )
    assert pointed == {
        "model_id": "composer-2.5",
        "label": "Composer 2.5",
        "highlighted": True,
        "current": None,
        "selected": None,
        "disabled": False,
    }
    picker_projection = adapter.project_observations(picker_evidence, prior=None).updates
    configuration = picker_projection["model_configuration"].value
    assert configuration is not None
    assert configuration.highlighted_model_id == "composer-2.5"
    assert configuration.selected_model_id is None
    assert configuration.configured_model_id is None
    # Visible rows are projected for exhaustive discovery; pagination metadata
    # prevents their viewport from being mistaken for the whole catalog.
    assert len(configuration.available) == PICKER_VISIBLE_CHOICES
    assert dict(configuration.parameters)["model_page_total"] == "27"
    assert len(picker["models"]["picker"]["choices"]) == PICKER_VISIBLE_CHOICES
    assert picker_projection["active_model"].knowledge is Knowledge.UNKNOWN

    # Moving the pointer changes only the highlighted parameter.  Checkmarks,
    # and therefore configured values, remain independent evidence.
    assert hovered_params["models"]["parameters"]["options"]["effort"] == (
        {"label": "low", "highlighted": True, "current": False},
        {"label": "medium", "highlighted": False, "current": False},
        {"label": "high", "highlighted": False, "current": True},
        {"label": "xhigh", "highlighted": False, "current": False},
        {"label": "max", "highlighted": False, "current": False},
    )
    assert current_params["models"]["parameters"]["options"]["effort"][1] == {
        "label": "medium",
        "highlighted": True,
        "current": True,
    }
    assert dict(hovered_params["models"]["parameters"]["values"])["effort"] == "high"
    assert dict(current_params["models"]["parameters"]["values"])["effort"] == "medium"
    # The parameter editor carries a durable staged configuration identity and
    # semantic option/navigation evidence; it is not mistaken for active model
    # readback or flattened into the shared snapshot.
    parameter_values = dict(current_params["models"]["parameters"]["values"])
    assert parameter_values["stage"] == "effort"
    assert parameter_values["configured_model_id"] == "opus-4-8"
    assert parameter_values["effort_option.medium"] == "1"
    assert parameter_values["effort_highlighted_index"] == "1"
    assert status["models"]["active_readback"] == {
        "model_id": "composer-2.5",
        "display_name": "Composer 2.5",
        "effort": "fast",
    }


def test_projection_prefers_http_usage_but_keeps_terminal_usage_evidence() -> None:
    adapter = CursorHarnessAdapter(
        http_usage={
            "source": "cursor-api:GetCurrentPeriodUsage",
            "plan": "pro",
            "windows": [{"name": "api", "percent_used": 12.5, "reset_at": "2026-07-12T00:00Z"}],
            "raw": {"api_used": 17, "provider_only": {"nested": True}},
        }
    )
    evidence = adapter.parse_evidence(_frame("cursor_tool_output.txt"), ())
    delta = adapter.project_observations(evidence, prior=None)

    frame_payload = evidence[0].payload
    usage = delta.updates["usage"]
    assert frame_payload["terminal_usage"]["context_percent"] == TERMINAL_CONTEXT_PERCENT
    assert evidence[1].evidence_type == "cursor.http_usage.v1"
    assert evidence[1].payload["status"]["raw"]["provider_only"] == {"nested": True}
    assert usage.value is not None
    assert usage.value.windows[0].name == "api"
    assert usage.value.windows[0].percent_used == HTTP_USAGE_PERCENT
    assert len(usage.evidence) == USAGE_EVIDENCE_COUNT


def test_tool_evidence_conservatively_records_obvious_file_reads_and_not_arbitrary_writes() -> None:
    evidence = CursorHarnessAdapter().parse_evidence(_frame("cursor_tool_output.txt"), ())
    tools = evidence[0].payload["tool_activity"]

    assert tools["paths_read"] == (".gitignore",)
    assert tools["paths_written"] == ()
    assert tools["commands"] == ("find /home/user/Documents/code/testingmurderharness -type f",)
    assert tools["transcript_tools"]


def test_cursor_lowering_returns_values_for_actuator_without_terminal_side_effects() -> None:
    adapter = CursorHarnessAdapter()
    snapshot = unknown_snapshot(HarnessId("cursor"), captured_at=NOW)
    clear = adapter.lower(ClearComposer("clear", "op", DuplicatePolicy.REPLAY_SAFE), snapshot)
    prompt = adapter.lower(
        InsertPromptPayload(
            "insert",
            "op",
            DuplicatePolicy.SAFE_BEFORE_COMMIT,
            (
                InputChunk("typed", InputProvenance.USER_TYPED, "one"),
                InputChunk("pasted", InputProvenance.USER_PASTE_BLOCK, "two"),
            ),
            "fingerprint",
        ),
        snapshot,
    )
    with pytest.raises(ValueError, match="current picker configuration evidence"):
        adapter.lower(
            SelectModel("model", "op", DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION, "gpt-5.5"),
            snapshot,
        )

    parameter_evidence = adapter.parse_evidence(
        _frame("cursor_opus_effort_medium_selected.txt"), ()
    )
    parameter_delta = adapter.project_observations(parameter_evidence, prior=None)
    parameter_snapshot = replace(
        snapshot,
        model_configuration=parameter_delta.updates["model_configuration"],
        surface=Observed.present(
            SurfaceState(
                SurfaceKind.MODEL_PICKER,
                frozenset({SurfaceKind.MODEL_PICKER}),
                SurfaceKind.MODEL_PICKER,
                True,
                True,
            ),
            evidence=(),
            observed_at=NOW,
            revision=snapshot.revision,
        ),
    )
    effort = adapter.lower(
        SelectModel(
            "effort",
            "op",
            DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION,
            "opus-4-8",
            effort="medium",
        ),
        parameter_snapshot,
    )

    assert clear == (SendNamedKey("clear:clear", "C-u"),)
    assert isinstance(prompt[0], SendLiteralKeys)
    assert isinstance(prompt[1], PasteBuffer)
    assert effort == (SendNamedKey("effort:select-effort", "Enter"),)


def test_cursor_july_radio_parameters_and_reasoning_status_are_model_aware() -> None:
    adapter = CursorHarnessAdapter()
    parameter_payload = adapter.parse_evidence(
        _frame("cursor_grok_effort_medium_selected.txt"), ()
    )[0].payload
    status_payload = adapter.parse_evidence(_frame("cursor_grok_status_high_fast.txt"), ())[
        0
    ].payload

    parameters = dict(parameter_payload["models"]["parameters"]["values"])
    assert parameters["configured_model_id"] == "cursor-grok-4-5"
    assert parameters["effort"] == "medium"
    assert parameters["effort_option.medium"] == "1"
    assert parameters["fast_enabled"] is False
    assert status_payload["models"]["active_readback"] == {
        "model_id": "cursor-grok-4-5",
        "display_name": "Cursor Grok 4.5",
        "effort": "high",
    }


def test_cursor_composer_speed_lowers_to_independent_fast_toggle() -> None:
    adapter = CursorHarnessAdapter()
    snapshot = unknown_snapshot(HarnessId("cursor"), captured_at=NOW)
    evidence = adapter.parse_evidence(_frame("cursor_composer_fast_off.txt"), ())
    delta = adapter.project_observations(evidence, prior=None)
    parameter_snapshot = replace(
        snapshot,
        model_configuration=delta.updates["model_configuration"],
        surface=delta.updates["surface"],
    )

    effects = adapter.lower(
        SelectModel(
            "fast",
            "op",
            DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION,
            "composer-2.5",
            fast_enabled=True,
        ),
        parameter_snapshot,
    )

    assert effects == (SendNamedKey("fast:toggle-fast", "Enter"),)


def test_cursor_opens_parameter_editor_with_tab_when_effort_is_requested() -> None:
    adapter = CursorHarnessAdapter()
    snapshot = unknown_snapshot(HarnessId("cursor"), captured_at=NOW)
    picker_evidence = adapter.parse_evidence(_frame("cursor_model_list_fast_active.txt"), ())
    picker_delta = adapter.project_observations(picker_evidence, prior=None)
    picker_snapshot = replace(
        snapshot,
        model_configuration=picker_delta.updates["model_configuration"],
        surface=picker_delta.updates["surface"],
    )

    effects = adapter.lower(
        SelectModel(
            "edit",
            "op",
            DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION,
            "gpt-5.5",
            effort="medium",
        ),
        picker_snapshot,
    )

    assert effects[-1] == SendNamedKey("edit:edit-model", "Tab")


def test_cursor_july_composer_row_and_parameter_frames_lower_distinct_actions() -> None:
    adapter = CursorHarnessAdapter()
    snapshot = unknown_snapshot(HarnessId("cursor"), captured_at=NOW)

    row_delta = adapter.project_observations(
        adapter.parse_evidence(_frame("cursor_july_composer_row.txt"), ()),
        prior=None,
    )
    row_snapshot = replace(
        snapshot,
        model_configuration=row_delta.updates["model_configuration"],
        surface=row_delta.updates["surface"],
    )
    activation = adapter.lower(
        SelectModel(
            "activate",
            "op",
            DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION,
            "composer-2.5",
        ),
        row_snapshot,
    )

    parameter_delta = adapter.project_observations(
        adapter.parse_evidence(_frame("cursor_july_composer_parameters.txt"), ()),
        prior=None,
    )
    parameters = parameter_delta.updates["model_configuration"].value
    assert parameters is not None
    assert dict(parameters.parameters) == {
        "stage": "effort",
        "configured_model_id": "composer-2.5",
        "fast_enabled": False,
    }
    parameter_snapshot = replace(
        snapshot,
        model_configuration=parameter_delta.updates["model_configuration"],
        surface=parameter_delta.updates["surface"],
    )
    dismissal = adapter.lower(
        DismissOverlay(
            "dismiss",
            "op",
            DuplicatePolicy.REPLAY_SAFE_WHILE_PRECONDITION_HOLDS,
            "model_picker",
        ),
        parameter_snapshot,
    )

    assert activation[-1] == SendNamedKey("activate:select", "Enter")
    assert all(not isinstance(effect, SendNamedKey) or effect.key != "Tab" for effect in activation)
    assert dismissal == (SendNamedKey("dismiss:escape", "Escape"),)


@pytest.mark.parametrize(("direction", "key"), (("down", "Down"), ("up", "Up")))
def test_cursor_lowers_model_picker_navigation_only_from_observed_picker(
    direction: str, key: str
) -> None:
    adapter = CursorHarnessAdapter()
    snapshot = unknown_snapshot(HarnessId("cursor"), captured_at=NOW)
    picker_evidence = adapter.parse_evidence(_frame("cursor_model_list_fast_active.txt"), ())
    picker_delta = adapter.project_observations(picker_evidence, prior=None)
    picker_snapshot = replace(
        snapshot,
        model_configuration=picker_delta.updates["model_configuration"],
        surface=picker_delta.updates["surface"],
    )
    action = NavigateModelPicker("navigate", "op", DuplicatePolicy.REPLAY_SAFE, direction)

    assert adapter.lower(action, picker_snapshot) == (SendNamedKey("navigate:navigate-model", key),)

    with pytest.raises(ValueError, match="requires a visible picker"):
        adapter.lower(action, snapshot)
