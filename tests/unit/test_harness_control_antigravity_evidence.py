import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import murder.llm.harness_control.adapters.antigravity as antigravity_module
from murder.llm.harness_control.adapters.antigravity import AntigravityHarnessAdapter
from murder.llm.harness_control.model.actions import DuplicatePolicy, SelectModel, SendNamedKey
from murder.llm.harness_control.model.evidence import FrameId, HarnessId, TerminalFrame
from murder.llm.harness_control.model.observations import Knowledge, unknown_snapshot

ROOT = Path(__file__).parents[2]


def _frame(name):
    return TerminalFrame(
        FrameId(name),
        HarnessId("antigravity"),
        datetime(2026, 7, 11, tzinfo=timezone.utc),
        220,
        50,
        (ROOT / "tests" / "fixtures" / "harness_panes" / name).read_text(),
        False,
        0,
        1,
    )


def test_quota_evidence_is_retained_and_projected():
    a, f = AntigravityHarnessAdapter(), _frame("agy_usage_dialog_grouped.txt")
    e = a.parse_evidence(f, ())
    quota = e[0].payload["quota"]
    groups = quota["groups"]
    usage = a.project_observations(
        e, unknown_snapshot(HarnessId("antigravity"), captured_at=f.captured_at)
    ).updates["usage"]

    assert e[0].parser_version == "antigravity-evidence-v3"
    assert e[0].evidence_type == "antigravity.frame.v3"
    assert e[0].payload["identity"] == {
        "account": "example@website.com",
        "plan": "Antigravity Starter Quota",
        "workspace": "~/Documents/code/testingmurderharness",
        "active_model_label": "Gemini 3.1 Pro (Low)",
    }
    assert groups == [
        {
            "label": "GEMINI MODELS",
            "members": ["Gemini Flash", "Gemini Pro"],
            "limit_label": "Weekly Limit",
            "remaining_percent": 85.61,
            "displayed_remaining_percent": 86.0,
            "status_text": "86% remaining · Refreshes in 157h 26m",
            "reset_text": "Refreshes in 157h 26m",
            "quota_available": False,
            "raw_lines": [
                "GEMINI MODELS",
                "Models within this group: Gemini Flash, Gemini Pro",
                "Weekly Limit",
                "[███████████████████████████████████████████░░░░░░░] 85.61%",
                "86% remaining · Refreshes in 157h 26m",
            ],
        },
        {
            "label": "CLAUDE AND GPT MODELS",
            "members": ["Claude Opus", "Claude Sonnet", "GPT-OSS"],
            "limit_label": "Weekly Limit",
            "remaining_percent": 100.0,
            "displayed_remaining_percent": None,
            "status_text": "Quota available",
            "reset_text": None,
            "quota_available": True,
            "raw_lines": [
                "CLAUDE AND GPT MODELS",
                "Models within this group: Claude Opus, Claude Sonnet, GPT-OSS",
                "Weekly Limit",
                "[██████████████████████████████████████████████████] 100.00%",
                "Quota available",
            ],
        },
    ]
    assert quota["windows"][0]["percent_used"] == pytest.approx(14.39)
    assert quota["windows"][0]["percent_used"] == pytest.approx(
        100.0 - groups[0]["remaining_percent"]
    )
    assert usage.knowledge is Knowledge.PRESENT
    assert usage.value is not None
    assert usage.value.windows[0].percent_used == pytest.approx(14.39)
    assert tuple(window.name for window in usage.value.windows) == (
        "Gemini Models",
        "Claude and GPT Models",
    )


def test_trust_is_permission_evidence():
    a, f = AntigravityHarnessAdapter(), _frame("agy_trust_dialog.txt")
    assert (
        a.project_observations(a.parse_evidence(f, ()), None)
        .updates["permission_request"]
        .knowledge
        is Knowledge.PRESENT
    )
    signing_in = _frame("agy_signing_in.txt")
    signing_surface = a.project_observations(a.parse_evidence(signing_in, ()), None).updates[
        "surface"
    ]
    assert (
        signing_surface.value.primary is antigravity_module.SurfaceKind.LOGIN_DIALOG
    )


def test_model_picker_distinguishes_cursor_configuration_and_active_readback() -> None:
    adapter, frame = AntigravityHarnessAdapter(), _frame("agy_model_picker.txt")
    delta = adapter.project_observations(
        adapter.parse_evidence(frame, ()),
        unknown_snapshot(HarnessId("antigravity"), captured_at=frame.captured_at),
    )
    configuration = delta.updates["model_configuration"]
    active = delta.updates["active_model"]

    assert configuration.knowledge is Knowledge.PRESENT
    assert configuration.value is not None
    assert configuration.value.configured_model_id == "gemini-3-1-pro"
    assert configuration.value.highlighted_model_id == "gemini-3-1-pro"
    assert configuration.value.selected_model_id is None
    assert configuration.value.parameters == (("effort", "low"),)
    assert active.knowledge is Knowledge.UNKNOWN
    assert active.value is None
    assert delta.semantic_events[0]["type"] == "antigravity.model_picker_visible"


def test_model_lowering_navigates_only_observed_antigravity_picker() -> None:
    adapter, frame = AntigravityHarnessAdapter(), _frame("agy_model_picker.txt")
    initial = unknown_snapshot(HarnessId("antigravity"), captured_at=frame.captured_at)
    delta = adapter.project_observations(adapter.parse_evidence(frame, ()), initial)
    snapshot = replace(
        initial,
        surface=delta.updates["surface"],  # type: ignore[arg-type]
        model_configuration=delta.updates["model_configuration"],  # type: ignore[arg-type]
    )
    action = SelectModel(
        "select",
        "model-op",
        DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION,
        "gemini-3-5-flash",
        effort="high",
    )

    assert adapter.lower(action, snapshot) == (
        SendNamedKey("select:nav:0", "Up"),
        SendNamedKey("select:nav:1", "Up"),
        SendNamedKey("select:confirm", "Enter"),
    )


def test_model_lowering_rejects_ambiguous_antigravity_variant_without_effort() -> None:
    adapter, frame = AntigravityHarnessAdapter(), _frame("agy_model_picker.txt")
    initial = unknown_snapshot(HarnessId("antigravity"), captured_at=frame.captured_at)
    delta = adapter.project_observations(adapter.parse_evidence(frame, ()), initial)
    snapshot = replace(
        initial,
        surface=delta.updates["surface"],  # type: ignore[arg-type]
        model_configuration=delta.updates["model_configuration"],  # type: ignore[arg-type]
    )
    action = SelectModel(
        "select", "model-op", DuplicatePolicy.AMBIGUOUS_AFTER_EMISSION, "gemini-3-5-flash"
    )

    with pytest.raises(ValueError, match="absent or ambiguous"):
        adapter.lower(action, snapshot)


def test_composer_truth_and_transcript_failure_are_independent_evidence_laws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = AntigravityHarnessAdapter()

    for fixture, expected_text in (("agy_idle.txt", ""), ("agy_resume_invalid.txt", "/resume")):
        frame = _frame(fixture)
        envelope = adapter.parse_evidence(frame, ())[0]
        composer = envelope.payload["composer"]
        observation = adapter.project_observations([envelope], None).updates["composer"]

        assert composer["exact_text"] == expected_text, fixture
        assert composer["normalized_text"] == expected_text, fixture
        assert composer["fingerprint"] == hashlib.sha256(
            expected_text.encode()
        ).hexdigest(), fixture
        assert observation.knowledge is Knowledge.PRESENT
        assert observation.value is not None
        assert observation.value.text == expected_text

    resume = adapter.parse_evidence(_frame("agy_resume_invalid.txt"), ())[0].payload["surfaces"][
        "resume"
    ]
    autocomplete = adapter.parse_evidence(_frame("agy_resume_invalid.txt"), ())[0].payload[
        "surfaces"
    ]["slash_autocomplete"]
    assert resume is None  # historical resume UI is not the final live surface
    assert autocomplete == {
        "typed_text": "/resume",
        "commands": [{"command": "/resume", "description": "Browse and resume past conversations"}],
    }

    malformed = TerminalFrame(
        FrameId("agy-no-composer"),
        HarnessId("antigravity"),
        datetime(2026, 7, 11, tzinfo=timezone.utc),
        80,
        2,
        "Antigravity CLI\nstatus only",
        False,
        0,
        2,
    )

    def fail_transcript(*_args, **_kwargs):
        raise RuntimeError("synthetic parser defect")

    monkeypatch.setattr(antigravity_module, "parse_frames", fail_transcript)
    envelope = adapter.parse_evidence(malformed, ())[0]
    observation = adapter.project_observations([envelope], None).updates["composer"]

    assert envelope.payload["raw_frame"]["text"] == malformed.raw_text
    assert envelope.payload["transcript"]["state"] == "unknown"
    assert envelope.diagnostics.messages == (
        "transcript parse failed: RuntimeError: synthetic parser defect",
    )
    assert envelope.payload["composer"]["exact_text"] is None
    assert observation.knowledge is Knowledge.UNKNOWN
    assert observation.value is None
