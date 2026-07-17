"""Pure Antigravity evidence parsing, projection, and lowering."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from murder.llm.harness_control.adapters.base import HarnessActionAdapter, HarnessObservationAdapter
from murder.llm.harness_control.model.actions import (
    FAST_HUMANIZED_TYPING,
    AnswerPermission,
    AnswerQuestion,
    ClearComposer,
    CommitPromptSubmission,
    ConfigureSessionSettings,
    DismissOverlay,
    InputProvenance,
    InsertPromptPayload,
    NavigateModelPicker,
    OpenModelPicker,
    PasteBuffer,
    RequestUsage,
    SelectModel,
    SemanticAction,
    SendInterrupt,
    SendLiteralKeys,
    SendNamedKey,
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
    ModelConfigurationState,
    ModelState,
    ObservationDelta,
    ObservationRevision,
    ObservationSnapshot,
    Observed,
    PermissionRequestState,
    QuestionState,
    SessionSettingsState,
    SurfaceKind,
    SurfaceState,
    ToolActivityState,
    ToolInteraction,
    TranscriptTailState,
    UsageState,
    UsageWindow,
)
from murder.llm.harnesses.parsing import (
    parse_antigravity_model_choices,
    slug_model_label,
    strip_ansi,
)
from murder.llm.harnesses.transcripts import parse_frames
from murder.llm.harnesses.usage import parse_antigravity_usage_pane

_MODEL = re.compile(
    r"(?P<label>(?:Gemini|Claude|GPT)[^\n]{2,60}?\((?:Low|Medium|High|Thinking)\))", re.I
)
_BUSY = re.compile(r"[⠀-⣿]\s+(?P<label>\w+)\.\.\.")
_COMPOSER_BORDER = re.compile(r"^[─━═-]{10,}\s*$")
_COMPOSER_RULE_COUNT = 2
_MINIMUM_DECISION_CHOICES = 2
_ACCOUNT = re.compile(r"(?P<account>[^\s@]+@[^\s@]+)\s*(?:\((?P<plan>[^)]*)\))?")
_WORKSPACE = re.compile(
    r"(?P<workspace>~/(?:[^\s]+)|/(?:tmp|home|Users|workspace|work|mnt)/[^\s]*)"
)
_WARNING = re.compile(r"\b(?:warning|error|failed|not found)\b", re.IGNORECASE)
_TOOL_LINE = re.compile(r"^\s*●\s+(?P<tool>[A-Za-z][\w-]*)\((?P<argument>.*?)\)")


class AntigravityHarnessAdapter(HarnessObservationAdapter, HarnessActionAdapter):
    parser_version = "antigravity-evidence-v3"

    def parse_evidence(
        self, frame: TerminalFrame, history: Sequence[EvidenceEnvelope]
    ) -> Sequence[EvidenceEnvelope]:
        del history
        clean = strip_ansi(frame.raw_text)
        # A terminal capture contains scrollback as well as the currently live
        # UI.  Control claims must be derived from the last rendered composer
        # region onward; transcript ingestion deliberately retains the whole
        # frame separately.
        view = _active_view(clean)
        diagnostics: list[str] = []
        try:
            transcript = parse_frames("antigravity", [frame.raw_text], pane_height=frame.height)
        except Exception as exc:  # retain broad frame evidence despite auxiliary parser defects
            transcript = {"harness": "antigravity", "segments": [], "state": "unknown"}
            diagnostics.append(f"transcript parse failed: {type(exc).__name__}: {exc}")
        choices = [
            {
                "label": c.label,
                "model_id": slug_model_label(re.sub(r"\s*\([^)]*\)", "", c.label)),
                "current": c.current,
                "highlighted": _agy_row_is_highlighted(clean, c.label),
                "effort": _effort(c.label),
            }
            for c in parse_antigravity_model_choices(view)
        ]
        quota = parse_antigravity_usage_pane(view, fetched_at=frame.captured_at.isoformat())
        trust = _trust(view)
        resume = _resume(view)
        autocomplete = _slash_autocomplete(view)
        identity = _identity(clean)
        notices = _notices(view)
        activity = _activity(view)
        question_surface, permission_surface = _agy_decision_surfaces(view)
        payload: dict[str, Any] = {
            "raw_frame": {
                "text": frame.raw_text,
                "ansi_preserved": frame.ansi_preserved,
                "pane_epoch": frame.pane_epoch,
                "capture_sequence": frame.capture_sequence,
            },
            "identity": identity,
            "composer": _composer(view),
            "transcript": transcript,
            "models": {
                "available": choices,
                "readbacks": [m.group("label") for m in _MODEL.finditer(view)],
                "configuration": _agy_model_configuration(choices),
            },
            "quota": {
                "windows": [
                    {
                        "name": w.name,
                        "percent_used": w.percent_used,
                        "reset_at": w.reset_at,
                        "used": w.used,
                        "limit": w.limit,
                    }
                    for w in quota.windows
                ],
                "groups": _agy_grouped_quota_evidence(view),
                "plan": quota.plan,
                "raw": quota.raw,
            },
            "surfaces": {
                "trust": trust,
                "login": "not signed in" in view.lower() or "signing in" in view.lower(),
                "usage": bool(quota.windows),
                "model_picker": bool(choices),
                "resume": resume,
                "rewind": "rewind" in view.lower(),
                "slash_autocomplete": autocomplete,
            },
            "context": {
                "lines": _context_lines(view),
            },
            "settings": _agy_settings(view),
            "activity": activity,
            "notices": notices,
            "question_surface": question_surface,
            "permission_surface": permission_surface,
            "question_ack": _agy_question_ack(clean),
            "permission_ack": _agy_permission_ack(clean),
        }
        return (
            EvidenceEnvelope(
                EvidenceId(f"antigravity:{frame.frame_id}:v3"),
                frame.frame_id,
                frame.harness_id,
                self.parser_version,
                frame.captured_at,
                "antigravity.frame.v3",
                payload,
                (ScreenRegionRef("full_frame", 1, len(clean.splitlines())),),
                EvidenceDiagnostics(self.parser_version, tuple(diagnostics)),
            ),
        )

    def project_observations(  # noqa: PLR0912, PLR0915 -- explicit surface projection
        self, evidence: Sequence[EvidenceEnvelope], prior: ObservationSnapshot | None
    ) -> ObservationDelta:
        e = next((x for x in reversed(evidence) if x.evidence_type == "antigravity.frame.v3"), None)
        if e is None:
            return ObservationDelta({})
        p, ref, now = e.payload, e.ref(), e.captured_at
        rev = ObservationRevision(
            p["raw_frame"]["pane_epoch"],
            p["raw_frame"]["capture_sequence"],
            prior.revision.semantic_sequence + 1 if prior else 1,
        )

        def present(v: object) -> Observed[object]:
            return Observed.present(v, evidence=(ref,), observed_at=now, revision=rev)

        def unknown(s: str) -> Observed[object]:
            return Observed.without_value(
                Knowledge.UNKNOWN, evidence=(ref,), observed_at=now, revision=rev, explanation=s
            )

        trust, busy = p["surfaces"]["trust"], p["activity"]["busy"]
        question_surface = p["question_surface"]
        permission_surface = p["permission_surface"]
        safe_composer = False
        if question_surface:
            surface = SurfaceState(
                SurfaceKind.QUESTION_PICKER,
                frozenset({SurfaceKind.QUESTION_PICKER}),
                SurfaceKind.QUESTION_PICKER,
                True,
                True,
            )
            modal = ModalState(
                ModalKind.QUESTION,
                question_surface["prompt"],
                _selected_index(question_surface["choices"]),
                len(question_surface["choices"]),
                True,
                True,
            )
            permission: Observed[object] = Observed.without_value(
                Knowledge.ABSENT, evidence=(ref,), observed_at=now, revision=rev
            )
            composer = unknown("question picker occludes composer")
        elif permission_surface:
            surface = SurfaceState(
                SurfaceKind.PERMISSION_DIALOG,
                frozenset({SurfaceKind.PERMISSION_DIALOG}),
                SurfaceKind.PERMISSION_DIALOG,
                True,
                True,
            )
            modal = ModalState(
                ModalKind.PERMISSION,
                permission_surface["description"],
                _selected_index(permission_surface["choices"]),
                len(permission_surface["choices"]),
                True,
                True,
            )
            permission = present(_agy_permission_state(permission_surface))
            composer = unknown("permission dialog occludes composer")
        elif trust:
            surface, modal = (
                SurfaceState(
                    SurfaceKind.TRUST_DIALOG,
                    frozenset({SurfaceKind.TRUST_DIALOG}),
                    SurfaceKind.TRUST_DIALOG,
                    True,
                    True,
                ),
                ModalState(
                    ModalKind.TRUST,
                    "Workspace trust",
                    trust["selected_index"],
                    len(trust["choices"]),
                    False,
                    True,
                ),
            )
            permission = present(
                PermissionRequestState(
                    "trust:" + hashlib.sha256(trust["prompt"].encode()).hexdigest()[:12],
                    "workspace",
                    None,
                    trust["prompt"],
                    tuple(
                        ChoiceState(
                            c["id"],
                            c["label"],
                            selected=c["selected"],
                            highlighted=c["selected"],
                            disabled=False,
                        )
                        for c in trust["choices"]
                    ),
                    trust["selected"],
                    frozenset({"trust", "workspace"}),
                )
            )
            composer = unknown("trust dialog occludes composer")
        elif p["surfaces"]["login"]:
            surface, modal = (
                SurfaceState(
                    SurfaceKind.LOGIN_DIALOG,
                    frozenset({SurfaceKind.LOGIN_DIALOG}),
                    SurfaceKind.LOGIN_DIALOG,
                    True,
                    True,
                ),
                ModalState(ModalKind.LOGIN, "Antigravity sign in", None, None, False, True),
            )
            permission, composer = (
                unknown("sign-in surface does not establish permission state"),
                unknown("sign-in surface occludes composer"),
            )
        elif p["surfaces"]["usage"]:
            surface, modal, permission, composer = (
                SurfaceState(
                    SurfaceKind.USAGE_PANEL,
                    frozenset({SurfaceKind.USAGE_PANEL}),
                    SurfaceKind.USAGE_PANEL,
                    True,
                    True,
                ),
                ModalState(ModalKind.USAGE, "Models & Quota", None, None, True, True),
                unknown("usage panel does not establish permission absence"),
                unknown("quota panel occludes composer"),
            )
        elif p["surfaces"]["model_picker"]:
            surface, modal, permission, composer = (
                SurfaceState(
                    SurfaceKind.MODEL_PICKER,
                    frozenset({SurfaceKind.MODEL_PICKER}),
                    SurfaceKind.MODEL_PICKER,
                    True,
                    True,
                ),
                ModalState(
                    ModalKind.MODEL_PICKER,
                    "Switch Model",
                    None,
                    len(p["models"]["available"]),
                    True,
                    True,
                ),
                unknown("model picker does not establish permission absence"),
                unknown("model picker occludes composer"),
            )
        elif p["surfaces"]["resume"]:
            surface, modal, permission, composer = (
                SurfaceState(
                    SurfaceKind.RESUME_PICKER,
                    frozenset({SurfaceKind.RESUME_PICKER}),
                    SurfaceKind.RESUME_PICKER,
                    True,
                    True,
                ),
                ModalState(
                    ModalKind.RESUME,
                    "Resume conversation",
                    p["surfaces"]["resume"]["selected_index"],
                    len(p["surfaces"]["resume"]["sessions"]),
                    True,
                    True,
                ),
                unknown("resume picker does not establish permission absence"),
                unknown("resume picker occludes composer"),
            )
        elif p["surfaces"]["slash_autocomplete"]:
            composer_evidence = p["composer"]
            surface, modal, permission, composer = (
                SurfaceState(
                    SurfaceKind.UNKNOWN_OVERLAY,
                    frozenset({SurfaceKind.COMPOSER, SurfaceKind.UNKNOWN_OVERLAY}),
                    SurfaceKind.UNKNOWN_OVERLAY,
                    False,
                    False,
                ),
                ModalState(
                    ModalKind.UNKNOWN, "Antigravity command autocomplete", None, None, True, False
                ),
                Observed.without_value(
                    Knowledge.ABSENT, evidence=(ref,), observed_at=now, revision=rev
                )
                if composer_evidence["visible"]
                else unknown("command autocomplete does not establish permission absence"),
                present(
                    ComposerState(
                        composer_evidence["exact_text"],
                        composer_evidence["normalized_text"],
                        composer_evidence["fingerprint"],
                        None,
                        True,
                        ComposerActionability.ACTIONABLE,
                        None,
                        True,
                    )
                )
                if composer_evidence["visible"]
                else unknown("command autocomplete hides the composer region"),
            )
            safe_composer = bool(composer_evidence["visible"])
        else:
            surface, modal, permission = (
                SurfaceState(
                    SurfaceKind.COMPOSER,
                    frozenset({SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}),
                    SurfaceKind.COMPOSER,
                    False,
                    False,
                ),
                None,
                Observed.without_value(
                    Knowledge.ABSENT, evidence=(ref,), observed_at=now, revision=rev
                ),
            )
            composer_evidence = p["composer"]
            composer = (
                present(
                    ComposerState(
                        composer_evidence["exact_text"],
                        composer_evidence["normalized_text"],
                        composer_evidence["fingerprint"],
                        None,
                        None,
                        ComposerActionability.VISIBLE_NOT_ACTIONABLE
                        if busy
                        else ComposerActionability.ACTIONABLE,
                        None,
                        not bool(busy),
                    )
                )
                if composer_evidence["visible"]
                else unknown("composer content region is not established by current evidence")
            )
            safe_composer = composer_evidence["visible"]
            if safe_composer:
                permission = Observed.without_value(
                    Knowledge.ABSENT, evidence=(ref,), observed_at=now, revision=rev
                )
        # Picker labels are configuration evidence, never active-runtime
        # readback.  Antigravity currently exposes no independent status
        # region in the picker fixtures, so fail closed rather than promote a
        # final menu row into activation proof.
        reads = p["models"]["readbacks"] if not p["models"]["available"] else []
        active = (
            present(
                ModelState(
                    slug_model_label(re.sub(r"\s*\([^)]*\)", "", reads[-1])),
                    _effort(reads[-1]),
                    reads[-1],
                )
            )
            if reads
            else unknown("no active model readback")
        )
        windows = tuple(
            UsageWindow(x["name"], x["percent_used"], _dt(x["reset_at"]), x["reset_at"])
            for x in p["quota"]["windows"]
        )
        events: list[dict[str, object]] = [
            {"type": "antigravity.notice", "kind": notice["kind"], "text": notice["text"]}
            for notice in p["notices"]
        ]
        if p["models"]["configuration"]["picker_visible"]:
            events.append(
                {
                    "type": "antigravity.model_picker_visible",
                    "stage": p["models"]["configuration"]["stage"],
                    "configured_model_id": p["models"]["configuration"]["configured_model_id"],
                    "highlighted_model_id": p["models"]["configuration"]["highlighted_model_id"],
                }
            )
        if p["surfaces"]["resume"]:
            events.append(
                {
                    "type": "antigravity.resume_picker_visible",
                    "search_text": p["surfaces"]["resume"]["search_text"],
                    "session_count": len(p["surfaces"]["resume"]["sessions"]),
                }
            )
        question_observed = (
            present(_agy_question_state(question_surface))
            if question_surface
            else present(
                replace(prior.question.value, answered_summary=(p["question_ack"],))
            )
            if p["question_ack"]
            and prior is not None
            and prior.question.knowledge is Knowledge.PRESENT
            and prior.question.value is not None
            else Observed.without_value(
                Knowledge.ABSENT if safe_composer or permission_surface else Knowledge.UNKNOWN,
                evidence=(ref,),
                observed_at=now,
                revision=rev,
                explanation=None
                if safe_composer or permission_surface
                else "no safe surface establishes question absence",
            )
        )
        if (
            not permission_surface
            and p["permission_ack"]
            and prior is not None
            and prior.permission_request.knowledge is Knowledge.PRESENT
            and prior.permission_request.value is not None
        ):
            prior_permission = prior.permission_request.value
            acknowledged = next(
                (
                    choice.stable_choice_id
                    for choice in prior_permission.choices
                    if p["permission_ack"] in choice.label.casefold()
                ),
                None,
            )
            if acknowledged is not None:
                permission = present(
                    replace(prior_permission, acknowledged_response_id=acknowledged)
                )
        return ObservationDelta(
            {
                "surface": present(surface),
                "modal": present(modal)
                if modal
                else Observed.without_value(
                    Knowledge.ABSENT, evidence=(ref,), observed_at=now, revision=rev
                ),
                "composer": composer,
                "generation": present(
                    GenerationState(
                        GenerationPhase.THINKING if busy else GenerationPhase.IDLE,
                        bool(busy),
                        bool(busy),
                        None,
                        None,
                        None,
                    )
                ),
                "permission_request": permission,
                "question": question_observed,
                "active_model": active,
                "model_configuration": _project_model_configuration(
                    p["models"]["configuration"], present, unknown
                ),
                "settings": present(SessionSettingsState(**p["settings"])),
                "usage": present(
                    UsageState(
                        None,
                        p["quota"]["plan"],
                        windows,
                        "CURRENT",
                        SurfaceKind.USAGE_PANEL if windows else None,
                        None,
                    )
                )
                if windows
                else unknown("no quota panel"),
                "tool_activity": present(_tool_activity(p["transcript"].get("segments", []))),
                "transcript_tail": present(
                    TranscriptTailState(
                        None,
                        None,
                        (),
                        bool(busy),
                        not bool(busy),
                        None,
                        len(p["transcript"].get("segments", [])),
                    )
                ),
            },
            (ref,),
            semantic_events=tuple(events),
        )

    def lower(  # noqa: PLR0911, PLR0912 - one branch per semantic action
        self, action: SemanticAction, snapshot: ObservationSnapshot
    ) -> Sequence[TerminalEffect]:
        if isinstance(action, InsertPromptPayload):
            return tuple(
                SendLiteralKeys(f"{action.action_id}:type:{i}", c.text, FAST_HUMANIZED_TYPING)
                if c.provenance is InputProvenance.USER_TYPED
                else PasteBuffer(f"{action.action_id}:paste:{i}", c.text)
                for i, c in enumerate(action.chunks)
            )
        if isinstance(action, ClearComposer):
            return (SendNamedKey(f"{action.action_id}:clear", "C-u"),)
        if isinstance(action, CommitPromptSubmission):
            return (SendNamedKey(f"{action.action_id}:commit", "Enter"),)
        if isinstance(action, RequestUsage):
            return (
                SendNamedKey(f"{action.action_id}:dismiss", "Escape"),
                SendLiteralKeys(f"{action.action_id}:usage", "/usage"),
                SendNamedKey(f"{action.action_id}:open-usage", "Enter"),
            )
        if isinstance(action, (DismissOverlay, SendInterrupt)):
            return (SendNamedKey(f"{action.action_id}:escape", "Escape"),)
        if isinstance(action, AnswerPermission):
            request = (
                snapshot.permission_request.value
                if snapshot.permission_request.knowledge is Knowledge.PRESENT
                else None
            )
            if request is None:
                raise ValueError("permission response requires observed dialog")
            return _agy_lower_choice(
                action.action_id,
                request.choices,
                action.response_id,
                action.response_label,
            )
        if isinstance(action, AnswerQuestion):
            if len(action.selections) != 1:
                raise ValueError("Antigravity currently exposes single-select questions")
            question = (
                snapshot.question.value
                if snapshot.question.knowledge is Knowledge.PRESENT
                else None
            )
            if question is None:
                raise ValueError("question response requires observed dialog")
            selection = action.selections[0]
            return _agy_lower_choice(
                action.action_id,
                question.choices,
                selection.stable_choice_id,
                selection.label,
            )
        if isinstance(action, SelectModel):
            return _lower_model_selection(action, snapshot)
        if isinstance(action, OpenModelPicker):
            return _open_model_picker(action, snapshot)
        if isinstance(action, NavigateModelPicker):
            if (
                snapshot.surface.knowledge is not Knowledge.PRESENT
                or snapshot.surface.value is None
                or snapshot.surface.value.primary is not SurfaceKind.MODEL_PICKER
            ):
                raise ValueError("Antigravity model-picker navigation requires a visible picker")
            return (
                SendNamedKey(
                    f"{action.action_id}:navigate-model",
                    "Down" if action.direction == "down" else "Up",
                ),
            )
        if isinstance(action, ConfigureSessionSettings):
            return _lower_session_settings(action, snapshot)
        raise ValueError(f"Antigravity lowering does not support {type(action).__name__}")


def _trust(clean: str) -> dict[str, Any] | None:
    if "Do you trust the contents" not in clean:
        return None
    rows = [
        {
            "id": str(i),
            "label": line.strip().lstrip(">").strip(),
            "selected": line.lstrip().startswith(">"),
        }
        for i, line in enumerate(clean.splitlines(), 1)
        if line.strip().lstrip(">").strip() in {"Yes, I trust this folder", "No, exit"}
    ]
    return {
        "prompt": "Do you trust the contents of this project?",
        "choices": rows,
        "selected": next((x["id"] for x in rows if x["selected"]), None),
        "selected_index": next((i for i, x in enumerate(rows) if x["selected"]), 0),
    }


def _agy_menu_rows(lines: list[str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    pattern = re.compile(r"^\s*(?P<pointer>>)?\s*(?P<number>\d+)\.\s+(?P<label>\S.*)$")
    for line in lines:
        if match := pattern.match(line):
            rows.append(
                {
                    "id": match.group("number"),
                    "number": int(match.group("number")),
                    "label": re.sub(r"\s+", " ", match.group("label")).strip(),
                    "highlighted": bool(match.group("pointer")),
                }
            )
    return rows


def _agy_decision_surfaces(
    view: str,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    question_matches = list(
        re.finditer(r"^\s*Question\s+\d+/\d+:\s*(?P<prompt>.+)$", view, re.I | re.M)
    )
    if question_matches and re.search(
        r"enter\s+Select", view[question_matches[-1].start() :], re.I
    ):
        match = question_matches[-1]
        suffix = view[match.start() :]
        rows = _agy_menu_rows(suffix.splitlines())
        if len(rows) >= _MINIMUM_DECISION_CHOICES:
            return {
                "prompt": match.group("prompt").strip(),
                "choices": rows,
                "selection_mode": "single",
                "allow_custom_answer": any(
                    "write-in" in str(row["label"]).casefold() for row in rows
                ),
            }, None
    permission_anchor = max(
        view.rfind("Do you want to proceed?"),
        view.rfind("Accept this file edit?"),
        view.rfind("Allow access to this file?"),
    )
    if permission_anchor >= 0 and re.search(
        r"(?:enter\s+Select|esc to cancel)", view[permission_anchor:], re.I
    ):
        suffix = view[permission_anchor:]
        rows = _agy_menu_rows(suffix.splitlines())
        if len(rows) >= _MINIMUM_DECISION_CHOICES:
            is_edit = any(
                marker in suffix.splitlines()[0].casefold()
                for marker in ("file edit", "access to this file")
            )
            command = next(
                (
                    line.partition(":")[2].strip()
                    for line in view[:permission_anchor].splitlines()
                    if "Requesting permission for:" in line or line.strip().startswith("Write:")
                ),
                None,
            )
            return None, {
                "description": suffix.splitlines()[0].strip(),
                "tool_name": "edit" if is_edit else "shell",
                "command": command,
                "choices": rows,
                "risks": ["write"] if is_edit else ["shell"],
            }
    return None, None


def _agy_question_ack(clean: str) -> str | None:
    matches = list(
        re.finditer(
            r"^\s*\?\s+(?P<prompt>[^\n]+)\n\s*>\s+(?P<answer>[^/\n][^\n]*)$",
            clean,
            re.M,
        )
    )
    return matches[-1].group("answer").strip() if matches else None


def _agy_permission_ack(clean: str) -> str | None:
    tail = "\n".join(clean.splitlines()[-60:]).casefold()
    return "no" if "user declined the tool call" in tail else None


def _selected_index(choices: list[dict[str, object]]) -> int | None:
    return next((index for index, choice in enumerate(choices) if choice["highlighted"]), None)


def _agy_question_state(surface: dict[str, object]) -> QuestionState:
    choices = surface["choices"]
    assert isinstance(choices, list)
    prompt = str(surface["prompt"])
    return QuestionState(
        hashlib.sha256(prompt.encode()).hexdigest()[:16],
        prompt,
        tuple(
            ChoiceState(
                str(row["id"]),
                str(row["label"]),
                number=int(row["number"]),
                highlighted=bool(row["highlighted"]),
                disabled=False,
            )
            for row in choices
        ),
        str(surface["selection_mode"]),
        None,
        (),
        bool(surface["allow_custom_answer"]),
        None,
        "Enter",
        "Escape",
        (),
    )


def _agy_permission_state(surface: dict[str, object]) -> PermissionRequestState:
    choices = surface["choices"]
    assert isinstance(choices, list)
    description = str(surface["description"])
    return PermissionRequestState(
        hashlib.sha256(description.encode()).hexdigest()[:16],
        str(surface["tool_name"]),
        str(surface["command"]) if surface.get("command") else None,
        description,
        tuple(
            ChoiceState(
                str(row["id"]),
                str(row["label"]),
                number=int(row["number"]),
                selected=bool(row["highlighted"]),
                highlighted=bool(row["highlighted"]),
                disabled=False,
            )
            for row in choices
        ),
        next((str(row["id"]) for row in choices if row["highlighted"]), None),
        frozenset(str(risk) for risk in surface["risks"]),
    )


def _agy_lower_choice(
    action_id: str,
    choices: tuple[ChoiceState, ...],
    target_id: str | None,
    target_label: str | None,
) -> tuple[TerminalEffect, ...]:
    target = next(
        (
            index
            for index, choice in enumerate(choices)
            if choice.stable_choice_id == target_id or choice.label == target_label
        ),
        None,
    )
    current = next((index for index, choice in enumerate(choices) if choice.highlighted), None)
    if target is None or current is None:
        raise ValueError("requested choice or current cursor is not visible")
    key = "Down" if target > current else "Up"
    return tuple(
        [
            *(
                SendNamedKey(f"{action_id}:nav:{index}", key)
                for index in range(abs(target - current))
            ),
            SendNamedKey(f"{action_id}:confirm", "Enter"),
        ]
    )


def _active_view(clean: str) -> str:
    """Return the live UI suffix without throwing away retained scrollback.

    The final pair of composer rules is a stable renderer boundary in Agy.  It
    separates old transcript/status panels from the current overlay.  Startup
    and trust surfaces do not have it, so their full frame remains visible.
    """

    lines = clean.splitlines()
    borders = [index for index, line in enumerate(lines) if _COMPOSER_BORDER.match(line)]
    if len(borders) >= _COMPOSER_RULE_COUNT:
        return "\n".join(lines[borders[-_COMPOSER_RULE_COUNT] :])
    return clean


def _identity(clean: str) -> dict[str, str | None]:
    account = plan = workspace = active_label = None
    for line in clean.splitlines():
        if match := _ACCOUNT.search(line):
            account = match.group("account")
            plan = match.group("plan") or plan
        elif match := _WORKSPACE.search(line):
            workspace = match.group("workspace")
        elif match := _MODEL.search(line):
            active_label = match.group("label")
    return {
        "account": account,
        "plan": plan,
        "workspace": workspace,
        "active_model_label": active_label,
    }


def _agy_settings(view: str) -> dict[str, str | bool | None]:
    lower = view.casefold()
    run_mode = (
        "plan"
        if "plan mode: research & plan only" in lower
        else "accept-edits"
        if "accept-edits mode: file edits auto-approved" in lower
        else "default"
    )
    return {"run_mode": run_mode, "fast_enabled": None}


def _resume(view: str) -> dict[str, object] | None:
    if not any(
        line.strip().casefold() == "conversations" or line.strip().casefold().startswith("search:")
        for line in view.splitlines()
    ):
        return None
    lines = view.splitlines()
    search = next(
        (
            line.partition(":")[2].strip()
            for line in lines
            if line.strip().casefold().startswith("search:")
        ),
        None,
    )
    sessions = [
        {"label": line.strip().lstrip(">").strip(), "highlighted": line.lstrip().startswith(">")}
        for line in lines
        if line.lstrip().startswith(">") and not line.lstrip().startswith("> /resume")
    ]
    return {
        "search_text": search,
        "empty_message": next(
            (line.strip() for line in lines if "no matching" in line.casefold()), None
        ),
        "sessions": sessions,
        "tabs": tuple(
            line.strip()
            for line in lines
            if "tab to cycle" in line.casefold() or "tab switch" in line.casefold()
        ),
        "selected_index": next((i for i, row in enumerate(sessions) if row["highlighted"]), None),
    }


def _slash_autocomplete(view: str) -> dict[str, object] | None:
    lines = view.splitlines()
    commands = [
        {"command": match.group("command"), "description": match.group("description").strip()}
        for line in lines
        if (match := re.match(r"^\s*>?\s*(?P<command>/[\w-]+)\s{2,}(?P<description>.+)$", line))
    ]
    if not commands:
        return None
    typed = next(
        (line.strip()[1:].strip() for line in lines if line.lstrip().startswith("> /")), None
    )
    return {"typed_text": typed, "commands": commands}


def _context_lines(view: str) -> list[dict[str, str]]:
    return [
        {"text": line.strip(), "category": "context" if "context" in line.casefold() else "tokens"}
        for line in view.splitlines()
        if "context" in line.casefold() or "token" in line.casefold()
    ]


def _activity(view: str) -> dict[str, object]:
    spinner = _BUSY.search(view)
    tool = next(
        (
            match.groupdict()
            for line in reversed(view.splitlines())
            if (match := _TOOL_LINE.match(line))
        ),
        None,
    )
    return {
        "busy": spinner.group("label") if spinner else None,
        "spinner_visible": spinner is not None,
        "current_tool": tool,
        "interrupted": "interrupted" in view.casefold(),
        "compaction": "compact" in view.casefold(),
    }


def _notices(view: str) -> list[dict[str, str]]:
    notices: list[dict[str, str]] = []
    for line in view.splitlines():
        text = line.strip()
        if not text or not _WARNING.search(text):
            continue
        lower = text.casefold()
        kind = "error" if "error" in lower or "failed" in lower else "warning"
        notices.append({"kind": kind, "text": text})
    return notices


_AGY_QUOTA_GROUP_RE = re.compile(r"^[A-Z][A-Z /&-]+MODELS$")
_AGY_QUOTA_BAR_RE = re.compile(r"\[[^]]+\]\s*(?P<remaining>\d+(?:\.\d+)?)%$")
_AGY_QUOTA_REMAINING_RE = re.compile(
    r"(?P<remaining>\d+(?:\.\d+)?)%\s+remaining(?:\s*[·|]\s*(?P<reset>Refreshes in .+))?$",
    re.IGNORECASE,
)


def _agy_grouped_quota_evidence(clean: str) -> list[dict[str, object]]:
    """Retain renderer-specific grouped quota facts before normalization.

    Antigravity renders percentages as quota *remaining*, whereas the shared
    usage view uses quota consumed.  Keeping the source polarity, membership,
    reset prose, and contributing lines here makes the conversion auditable.
    """

    lower = clean.casefold()
    anchor = lower.rfind("models & quota")
    if anchor < 0:
        return []
    lines = clean[anchor:].splitlines()
    groups: list[dict[str, object]] = []
    index = 0
    while index < len(lines):
        label = lines[index].strip()
        if not _AGY_QUOTA_GROUP_RE.fullmatch(label):
            index += 1
            continue

        raw_lines = [label]
        members: list[str] = []
        limit_label: str | None = None
        remaining_percent: float | None = None
        displayed_remaining_percent: float | None = None
        status_text: str | None = None
        reset_text: str | None = None
        quota_available = False
        cursor = index + 1
        while cursor < len(lines):
            stripped = lines[cursor].strip()
            if _AGY_QUOTA_GROUP_RE.fullmatch(stripped) or "esc to cancel" in stripped.casefold():
                break
            if stripped.startswith("Models within this group:"):
                raw_lines.append(stripped)
                members = [
                    item.strip() for item in stripped.partition(":")[2].split(",") if item.strip()
                ]
            elif stripped.casefold().endswith("limit"):
                raw_lines.append(stripped)
                limit_label = stripped
            elif match := _AGY_QUOTA_BAR_RE.search(stripped):
                raw_lines.append(stripped)
                remaining_percent = float(match.group("remaining"))
            elif match := _AGY_QUOTA_REMAINING_RE.search(stripped):
                raw_lines.append(stripped)
                status_text = stripped
                displayed_remaining_percent = float(match.group("remaining"))
                reset_text = match.group("reset")
                if remaining_percent is None:
                    remaining_percent = displayed_remaining_percent
            elif stripped.casefold() == "quota available":
                raw_lines.append(stripped)
                status_text = stripped
                quota_available = True
                if remaining_percent is None:
                    remaining_percent = 100.0
            cursor += 1

        groups.append(
            {
                "label": label,
                "members": members,
                "limit_label": limit_label,
                "remaining_percent": remaining_percent,
                "displayed_remaining_percent": displayed_remaining_percent,
                "status_text": status_text,
                "reset_text": reset_text,
                "quota_available": quota_available,
                "raw_lines": raw_lines,
            }
        )
        index = max(cursor, index + 1)
    return groups


def _composer(clean: str) -> dict[str, object]:
    """Recover only a delimiter-bounded Antigravity input region.

    A transcript line may also begin with ``>``. Requiring the surrounding
    renderer borders prevents a prior user turn from being mistaken for the
    current composer and prevents a missing region from becoming empty text.
    """

    lines = clean.splitlines()
    borders = [index for index, line in enumerate(lines) if _COMPOSER_BORDER.match(line)]
    regions = [
        (start, end)
        for start, end in zip(borders, borders[1:], strict=False)
        if end > start + 1 and lines[start + 1].lstrip().startswith(">")
    ]
    if not regions:
        return {
            "visible": False,
            "exact_text": None,
            "normalized_text": None,
            "fingerprint": None,
        }
    start, end = regions[-1]
    content = lines[start + 1 : end]
    first = content[0].lstrip()[1:]
    if first.startswith(" "):
        first = first[1:]
    exact = "\n".join((first, *content[1:])).rstrip("\n")
    normalized = re.sub(r"\s+", " ", exact).strip()
    return {
        "visible": True,
        "exact_text": exact,
        "normalized_text": normalized,
        "fingerprint": hashlib.sha256(normalized.encode()).hexdigest(),
    }


def _effort(label: str) -> str | None:
    m = re.search(r"\(([^)]+)\)", label)
    return m.group(1).lower() if m else None


def _agy_row_is_highlighted(clean: str, label: str) -> bool:
    """Read the cursor glyph without conflating it with ``(current)``."""

    target = re.sub(r"\s+", " ", label).strip()
    return any(
        line.lstrip().startswith(">") and re.sub(r"\s+", " ", line.lstrip()[1:]).strip() == target
        for line in clean.splitlines()
    )


def _agy_model_configuration(choices: list[dict[str, Any]]) -> dict[str, Any]:
    configured = next((row["model_id"] for row in choices if row["current"]), None)
    highlighted = next((row["model_id"] for row in choices if row["highlighted"]), None)
    current_row = next((row for row in choices if row["current"]), None)
    parameters: list[tuple[str, str | bool | None]] = []
    if current_row and current_row["effort"] is not None:
        parameters.append(("effort", current_row["effort"]))
    return {
        "picker_visible": bool(choices),
        "stage": "model" if choices else "none",
        "available": choices,
        "highlighted_model_id": highlighted,
        # Agy's cursor is merely navigation state, never committed selection.
        "selected_model_id": None,
        "configured_model_id": configured,
        "pending_changes": False if choices else None,
        "parameters": parameters,
    }


def _project_model_configuration(
    configuration: dict[str, Any], present: Any, unknown: Any
) -> Observed[object]:
    if not configuration["picker_visible"]:
        return unknown("no Antigravity model-picker configuration is visible")
    return present(
        ModelConfigurationState(
            tuple(
                ChoiceState(
                    row["model_id"],
                    row["label"],
                    selected=False,
                    highlighted=row["highlighted"],
                    current=row["current"],
                )
                for row in configuration["available"]
            ),
            configuration["highlighted_model_id"],
            configuration["selected_model_id"],
            configuration["configured_model_id"],
            configuration["pending_changes"],
            tuple(tuple(item) for item in configuration["parameters"]),
        )
    )


def _lower_model_selection(
    action: SelectModel, snapshot: ObservationSnapshot
) -> Sequence[TerminalEffect]:
    """Navigate one currently observed Antigravity picker deterministically."""

    config = snapshot.model_configuration
    if config.knowledge is not Knowledge.PRESENT or config.value is None:
        raise ValueError(
            "Antigravity model selection requires current picker configuration evidence"
        )
    if snapshot.surface.knowledge is not Knowledge.PRESENT or snapshot.surface.value is None:
        raise ValueError("Antigravity model selection requires a known current surface")
    if snapshot.surface.value.primary is not SurfaceKind.MODEL_PICKER:
        raise ValueError("Antigravity model selection will not reopen an unobserved picker")
    candidates = [
        choice for choice in config.value.available if choice.stable_choice_id == action.model_id
    ]
    if action.effort is not None:
        candidates = [
            choice for choice in candidates if _effort(choice.label) == action.effort.casefold()
        ]
    if len(candidates) != 1:
        raise ValueError("Antigravity target model/effort is absent or ambiguous in current picker")
    target = candidates[0]
    if target.disabled is True:
        raise ValueError("Antigravity target model is disabled")
    choices = config.value.available
    current = next((index for index, choice in enumerate(choices) if choice.highlighted), None)
    if current is None:
        raise ValueError("Antigravity picker cursor is not visible; navigation would be ambiguous")
    target_index = choices.index(target)
    key = "Down" if target_index > current else "Up"
    return tuple(
        [
            *(
                SendNamedKey(f"{action.action_id}:nav:{index}", key)
                for index in range(abs(target_index - current))
            ),
            SendNamedKey(f"{action.action_id}:confirm", "Enter"),
        ]
    )


def _open_model_picker(
    action: OpenModelPicker, snapshot: ObservationSnapshot
) -> Sequence[TerminalEffect]:
    if snapshot.surface.knowledge is not Knowledge.PRESENT or snapshot.surface.value is None:
        raise ValueError("Antigravity model picker requires a known safe surface")
    if snapshot.surface.value.primary not in {SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}:
        raise ValueError("Antigravity model picker will not replace an unobserved overlay")
    return (
        SendLiteralKeys(f"{action.action_id}:open-model", "/model", FAST_HUMANIZED_TYPING),
        SleepEffect(f"{action.action_id}:await-autocomplete", timedelta(milliseconds=400)),
        SendNamedKey(f"{action.action_id}:open-model-enter", "Enter"),
    )


def _lower_session_settings(
    action: ConfigureSessionSettings, snapshot: ObservationSnapshot
) -> Sequence[TerminalEffect]:
    observed = snapshot.settings
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        raise ValueError("Antigravity settings require current live chrome readback")
    current = observed.value
    if action.fast_enabled is not None:
        raise ValueError("Antigravity does not expose an inference fast-mode setting")
    if action.run_mode is None or action.run_mode == current.run_mode:
        raise ValueError("Antigravity settings action has no observable change to apply")
    modes = ("default", "accept-edits", "plan")
    if current.run_mode not in modes or action.run_mode not in modes:
        raise ValueError("Antigravity run mode is unknown or unsupported")
    steps = (modes.index(action.run_mode) - modes.index(current.run_mode)) % len(modes)
    return tuple(
        SendNamedKey(f"{action.action_id}:cycle-mode:{index}", "BTab")
        for index in range(steps)
    )


def _tool_activity(segments: object) -> ToolActivityState:
    """Project only parser-declared tool calls; never infer filesystem effects."""

    rows = segments if isinstance(segments, list) else []
    tools = [row for row in rows if isinstance(row, dict) and row.get("type") == "tool_call"]
    return ToolActivityState(
        (),
        tuple(
            ToolInteraction(
                str(row.get("title")) if row.get("title") else None,
                str(row.get("command")) if row.get("command") else None,
                (),
                (),
                str(row.get("status") or "complete"),
                None,
                str(row.get("output")) if row.get("output") else None,
            )
            for row in tools[-8:]
        ),
    )


def _dt(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


__all__ = ["AntigravityHarnessAdapter"]
