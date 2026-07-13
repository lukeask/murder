"""Pi evidence breadth, narrow projection, and pure lowering."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

import murder.llm.harness_control.adapters.pi as pi_module
from murder.llm.harness_control.adapters.pi import PiHarnessAdapter
from murder.llm.harness_control.model.actions import (
    CommitPromptSubmission,
    DuplicatePolicy,
    SelectModel,
)
from murder.llm.harness_control.model.evidence import TerminalFrame
from murder.llm.harness_control.model.observations import (
    ComposerActionability,
    GenerationPhase,
    Knowledge,
    unknown_snapshot,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "harness_panes"
NOW = datetime(2026, 7, 11, tzinfo=timezone.utc)
MINIMUM_MODEL_ROWS = 2


def _evidence(name: str):
    frame = TerminalFrame(
        f"pi-{name}", "pi", NOW, 220, 50, (FIXTURES / name).read_text(), False, 0, 1
    )
    return PiHarnessAdapter().parse_evidence(frame, [])


def test_pi_model_scope_options_and_footer_remain_in_broad_evidence() -> None:
    evidence = _evidence("pi_idle.txt")
    payload = evidence[0].payload
    assert evidence[0].parser_version == "pi-evidence/v3"
    assert evidence[0].evidence_type == "pi.frame.v3"
    assert "deepseek/deepseek-v4-pro" in payload["model_scope"]["available"]
    assert payload["raw_frame"]["ansi_preserved"] is False
    assert len(payload["model_picker"]["rows"]) >= MINIMUM_MODEL_ROWS
    assert payload["active_model"]["provider"] == "inferno"
    assert payload["footer"]["context"] == "0.0%/66k"
    assert payload["footer"]["context_usage"] == {
        "percent_used": 0.0,
        "context_window_tokens": 66_000,
        "mode": "auto",
        "raw": "0.0%/66k (auto)",
    }
    current = next(row for row in payload["model_picker"]["rows"] if row["current"])
    assert current["stable_choice_id"] == "Qwen3.6-27B-Q8_0.gguf"
    assert current["display_name"] == "Qwen 3.6 27B Q8 (Local)"

    delta = PiHarnessAdapter().project_observations(evidence, None)
    assert delta.updates["active_model"].knowledge is Knowledge.PRESENT
    assert delta.updates["model_configuration"].value.available
    projected_current = next(
        choice
        for choice in delta.updates["model_configuration"].value.available
        if choice.current
    )
    assert projected_current.stable_choice_id == current["stable_choice_id"]
    assert projected_current.label == current["display_name"]
    assert "model_scope" not in delta.updates


def test_pi_resume_warnings_update_and_compaction_are_retained_without_false_projection() -> None:
    resume = _evidence("pi_resume_invalid.txt")[0].payload
    assert resume["resume"]["visible"] is True
    assert resume["resume"]["scope"] == "Current Folder"
    assert resume["resume"]["empty"] is True
    assert any("No session found" in row["text"] for row in resume["startup_warnings"])
    assert resume["resume"]["filter_text"] == "00000000-0000-0000-0000-000000000000"
    assert resume["resume"]["scope_options"] == ("current_folder", "all")

    busy = _evidence("pi_busy.txt")[0].payload
    assert busy["update_notices"]
    assert busy["startup_warnings"]
    assert "Update Available" in busy["raw_frame"]["text"]
    assert busy["status_lines"]


def test_pi_composer_projection_distinguishes_actionable_empty_from_picker_occlusion() -> None:
    adapter = PiHarnessAdapter()

    idle = adapter.project_observations(_evidence("pi_interrupt.txt"), None).updates["composer"]
    assert idle.knowledge is Knowledge.PRESENT
    assert idle.value.text == ""
    assert idle.value.normalized_text == ""
    assert idle.value.content_fingerprint == hashlib.sha256(b"").hexdigest()
    assert idle.value.actionability is ComposerActionability.ACTIONABLE
    assert idle.value.accepts_submission is True
    interrupted_generation = adapter.project_observations(
        _evidence("pi_interrupt.txt"), None
    ).updates["generation"].value
    assert interrupted_generation.phase is GenerationPhase.STOPPED
    assert interrupted_generation.active is False

    picker = adapter.project_observations(_evidence("pi_idle.txt"), None).updates["composer"]
    assert picker.knowledge is Knowledge.UNKNOWN

    busy_generation = adapter.project_observations(
        _evidence("pi_busy.txt"), None
    ).updates["generation"].value
    assert busy_generation.phase is GenerationPhase.STREAMING
    assert busy_generation.active is True


def test_pi_transcript_parser_failure_preserves_other_evidence_and_diagnostic(monkeypatch) -> None:
    def broken_parser(*_args, **_kwargs):
        raise ValueError("broken ANSI sequence")

    monkeypatch.setattr(pi_module, "parse_frames", broken_parser)
    envelope = _evidence("pi_interrupt.txt")[0]

    assert envelope.payload["transcript"]["state"] == "unknown"
    assert envelope.payload["composer"]["visible"] is True
    assert envelope.payload["footer"]["context"] == "0.0%/1.0M"
    assert "broken ANSI sequence" in envelope.diagnostics.messages[-1]

    monkeypatch.setattr(
        pi_module,
        "parse_frames",
        lambda *_args, **_kwargs: {
            "harness": "pi",
            "state": "idle",
            "segments": [
                {
                    "type": "tool_call",
                    "title": "read_file",
                    "command": "read_file README.md",
                    "status": "complete",
                    "output": "contents",
                }
            ],
        },
    )
    tool_evidence = _evidence("pi_interrupt.txt")
    tool_state = PiHarnessAdapter().project_observations(tool_evidence, None).updates[
        "tool_activity"
    ].value
    assert tool_evidence[0].payload["transcript"]["segments"][0]["output"] == "contents"
    assert tool_state.recent[0].completed_at is None


def test_pi_lowering_contains_effect_values_only() -> None:
    adapter = PiHarnessAdapter()
    snapshot = unknown_snapshot("pi", captured_at=NOW)
    with pytest.raises(ValueError, match="current picker configuration evidence"):
        adapter.lower(
            SelectModel(
                "model", "operation", DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION, "openai/gpt-5.5"
            ),
            snapshot,
        )
    assert (
        adapter.lower(
            CommitPromptSubmission("commit", "operation", DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION),
            snapshot,
        )[0].key
        == "Enter"
    )
