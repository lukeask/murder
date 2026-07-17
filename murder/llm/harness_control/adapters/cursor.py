"""Cursor edge adapter: broad frame evidence, narrow projection, pure lowering.

This module deliberately does not import Cursor's procedural adapter or tmux.
It interprets what Cursor rendered and returns terminal *values* for the shared
actuator to emit later.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from murder.llm.harness_control.adapters.base import HarnessActionAdapter, HarnessObservationAdapter
from murder.llm.harness_control.model.actions import (
    FAST_HUMANIZED_TYPING,
    ClearComposer,
    CommitPromptSubmission,
    DelayProfile,
    DismissOverlay,
    InputProvenance,
    InsertPromptPayload,
    NavigateModelPicker,
    OpenModelPicker,
    PasteBuffer,
    RequestUsage,
    RestoreComposer,
    SelectModel,
    SemanticAction,
    SendInterrupt,
    SendLiteralKeys,
    SendNamedKey,
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
    SurfaceKind,
    SurfaceState,
    ToolActivityState,
    ToolInteraction,
    TranscriptTailState,
    TurnRef,
    UsageState,
    UsageWindow,
)
from murder.llm.harnesses.parsing import (
    normalize_effort,
    parse_cursor_model_list,
    parse_cursor_model_page,
    strip_ansi,
)
from murder.llm.harnesses.transcripts import parse_frames

_INPUT = re.compile(r"^\s*→\s*(?P<text>.*?)(?:\s+ctrl\+c to stop)?\s*$", re.I)
_BUSY = re.compile(r"(?:\bComposing\b|\bRunning\b|ctrl\+c to stop)", re.I)
_STATUS = re.compile(r"^\s*(?P<label>.+?)\s+(?:Auto-run|Run\s+Everything)\s*$", re.I)
_MODEL_PAGE = re.compile(r"^\s*(?P<start>\d+)-(?P<end>\d+)\s+of\s+(?P<total>\d+)", re.I)
_ATTACHMENT = re.compile(
    r"^\s*\[(?P<label>Pasted text #(?P<ordinal>\d+)(?:\s*\+(?P<lines>\d+)\s+lines?)?)\]",
    re.I | re.M,
)
_WORKSPACE = re.compile(r"^\s*(?P<path>(?:~/|/|\./)[^·\n]+?)(?:\s+·\s+(?P<branch>\S+))?\s*$")
_RESUME_TITLE = re.compile(r"^\s*Previous Sessions\s*$", re.I | re.M)
_PARAMETER_TITLE = re.compile(
    r"^\s*(?P<model>.+?)\s+[—-]\s+Edit Parameters(?:\s+.*)?$", re.I | re.M
)
_CHECKED = re.compile(r"^\s*(?P<pointer>[→>])?\s*\[(?P<mark>[xX✓ ])\]\s*(?P<name>.+?)\s*$", re.M)
_OPTION = re.compile(
    r"^\s*(?P<pointer>[→>])?\s*"
    r"(?P<radio>[○●◯◉])?\s*"
    r"(?P<label>Low|Medium|High|Extra High|Max|\d+[KMG])\s*(?P<current>✓)?\s*$",
    re.I | re.M,
)
_FAST_RADIO = re.compile(
    r"^\s*(?P<pointer>[→>])?\s*(?P<radio>[○●◯◉])\s*Fast\s*(?P<current>✓)?\s*$",
    re.I | re.M,
)
_TOKEN_COUNT = re.compile(r"\b(?P<count>\d+)\s+tokens?\b", re.I)
_READ = re.compile(r"^\s*Read\s+(?P<path>[^\s`]+)\s*$", re.I | re.M)
_WRITE = re.compile(
    r"^\s*(?:Edited|Wrote|Created|Deleted|Renamed)\s+(?P<path>[^\s`]+)\s*$",
    re.I | re.M,
)
_SHELL = re.compile(r"^\s*\$\s+(?P<command>.+?)(?:\s+\d+(?:\.\d+)?[ms]?s.*)?$", re.M)
_CURSOR_FILTER_TYPING = DelayProfile(20.0, 45.0)


def _model_id(label: str) -> str | None:
    cleaned = re.sub(r"\s*\(Tab to modify\)\s*$", "", label, flags=re.I).strip()
    low = cleaned.casefold()
    if low.startswith("composer 2.5"):
        return "composer-2.5"
    if re.match(r"composer 2\b", low):
        return "composer-2"
    if low.startswith("auto"):
        return "auto"
    if not cleaned or low.startswith(("filter", "type to filter", "available models")):
        return None
    return re.sub(r"[^a-z0-9]+", "-", low).strip("-") or None


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _model_filter_label(model_id: str) -> str:
    """Recover Cursor's searchable display spelling from its stable slug."""
    tokens = model_id.split("-")
    rendered: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.isdigit() and index + 1 < len(tokens) and tokens[index + 1].isdigit():
            rendered.append(f"{token}.{tokens[index + 1]}")
            index += 2
            continue
        rendered.append("GPT" if token.casefold() == "gpt" else token.title())
        index += 1
    label = " ".join(rendered)
    match rendered:
        case ["GPT", version, *suffix]:
            label = f"GPT-{version}"
            if suffix:
                label += f" {' '.join(suffix)}"
    return label


class CursorHarnessAdapter(HarnessObservationAdapter, HarnessActionAdapter):
    """Cursor-specific observation and action adapter with no orchestration."""

    parser_version = "cursor-evidence-v3"

    def __init__(self, *, http_usage: Mapping[str, Any] | None = None) -> None:
        # The HTTP payload is externally collected authoritative side-channel
        # evidence. Copy the outer mapping so a caller cannot mutate its source
        # after evidence construction; nested high-cardinality raw fields stay
        # intentionally intact for persistence/reprocessing.
        self._http_usage = dict(http_usage) if http_usage is not None else None

    def parse_evidence(
        self, frame: TerminalFrame, history: Sequence[EvidenceEnvelope]
    ) -> Sequence[EvidenceEnvelope]:
        del history
        clean = strip_ansi(frame.raw_text)
        diagnostics: list[str] = []
        try:
            transcript = parse_frames("cursor", [frame.raw_text], pane_height=frame.height)
        except Exception as exc:  # broad evidence must survive an auxiliary parse failure
            transcript = {"harness": "cursor", "state": "unknown", "segments": []}
            diagnostics.append(f"transcript parse failed: {type(exc).__name__}: {exc}")
        payload = {
            "raw_frame": _raw_frame(frame),
            "composer": _composer(clean),
            "models": _models(clean),
            "workspace": _workspace(clean),
            "context": _context_evidence(clean),
            "resume_picker": _resume_picker(clean),
            "transcript": transcript,
            "tool_activity": _tool_activity(clean, transcript),
            "terminal_usage": _terminal_usage(clean),
            "notices": _notices(clean),
        }
        full = ScreenRegionRef("full_frame", 1, max(1, len(clean.splitlines())))
        result: list[EvidenceEnvelope] = [
            EvidenceEnvelope(
                EvidenceId(f"cursor:{frame.frame_id}:frame:v3"),
                frame.frame_id,
                frame.harness_id,
                self.parser_version,
                frame.captured_at,
                "cursor.frame.v3",
                payload,
                (full,),
                EvidenceDiagnostics(self.parser_version, tuple(diagnostics)),
            )
        ]
        if self._http_usage is not None:
            result.append(
                EvidenceEnvelope(
                    EvidenceId(f"cursor:{frame.frame_id}:http-usage:v1"),
                    frame.frame_id,
                    frame.harness_id,
                    self.parser_version,
                    frame.captured_at,
                    "cursor.http_usage.v1",
                    {
                        "provenance": "cursor-http-authoritative",
                        "status": self._http_usage,
                        "raw_frame_id": str(frame.frame_id),
                    },
                    (ScreenRegionRef("cursor_http_usage"),),
                    EvidenceDiagnostics(self.parser_version),
                )
            )
        return tuple(result)

    def project_observations(
        self, evidence: Sequence[EvidenceEnvelope], prior: ObservationSnapshot | None
    ) -> ObservationDelta:
        frame = next(
            (entry for entry in reversed(evidence) if entry.evidence_type == "cursor.frame.v3"),
            None,
        )
        if frame is None:
            return ObservationDelta(updates={}, diagnostics=("no Cursor frame evidence",))
        p, ref, now = frame.payload, frame.ref(), frame.captured_at
        raw = p["raw_frame"]
        revision = ObservationRevision(
            int(raw["pane_epoch"]),
            int(raw["capture_sequence"]),
            (prior.revision.semantic_sequence + 1) if prior is not None else 1,
        )

        def present(value: object) -> Observed[object]:
            return Observed.present(value, evidence=(ref,), observed_at=now, revision=revision)

        def without(knowledge: Knowledge, explanation: str) -> Observed[object]:
            return Observed.without_value(
                knowledge,
                evidence=(ref,),
                observed_at=now,
                revision=revision,
                explanation=explanation,
            )

        models = p["models"]
        composer = p["composer"]
        busy = bool(p["tool_activity"]["busy"])
        if models["picker"]["visible"]:
            surface = SurfaceState(
                SurfaceKind.MODEL_PICKER,
                frozenset({SurfaceKind.MODEL_PICKER}),
                SurfaceKind.MODEL_PICKER,
                True,
                True,
            )
            modal = ModalState(
                ModalKind.MODEL_PICKER,
                "Available models",
                None,
                len(models["picker"]["choices"]),
                True,
                True,
            )
            composer_observed = without(Knowledge.UNKNOWN, "Cursor model picker occludes composer")
        elif models["parameters"]["visible"]:
            surface = SurfaceState(
                SurfaceKind.MODEL_PICKER,
                frozenset({SurfaceKind.MODEL_PICKER}),
                SurfaceKind.MODEL_PICKER,
                True,
                True,
            )
            modal = ModalState(
                ModalKind.MODEL_PICKER, "Cursor model parameters", None, None, True, True
            )
            composer_observed = without(
                Knowledge.UNKNOWN, "Cursor parameter editor occludes composer"
            )
        elif p["resume_picker"]["visible"]:
            surface = SurfaceState(
                SurfaceKind.RESUME_PICKER,
                frozenset({SurfaceKind.RESUME_PICKER}),
                SurfaceKind.RESUME_PICKER,
                True,
                True,
            )
            modal = ModalState(ModalKind.RESUME, "Previous Sessions", None, None, True, True)
            composer_observed = without(Knowledge.UNKNOWN, "Cursor resume picker occludes composer")
        else:
            surface = SurfaceState(
                SurfaceKind.COMPOSER,
                frozenset({SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}),
                SurfaceKind.COMPOSER,
                False,
                False,
            )
            modal = None
            composer_observed = (
                present(
                    ComposerState(
                        composer["text"],
                        composer["normalized_text"],
                        composer["fingerprint"],
                        composer["cursor_visible"],
                        composer["focused"],
                        ComposerActionability.VISIBLE_NOT_ACTIONABLE
                        if busy
                        else ComposerActionability.ACTIONABLE,
                        composer["partial"],
                        not busy,
                        composer["queued_follow_up"],
                        tuple(composer["attachments"]),
                    )
                )
                if composer["visible"]
                else without(Knowledge.UNKNOWN, "Cursor composer is not visible")
            )

        active = models["active_readback"]
        updates: dict[str, Observed[object]] = {
            "surface": present(surface),
            "composer": composer_observed,
            "modal": present(modal)
            if modal
            else without(Knowledge.ABSENT, "no Cursor modal visible"),
            "generation": present(_generation(p["tool_activity"], p["terminal_usage"])),
            "transcript_tail": present(_tail(p["transcript"], busy)),
            "question": without(
                Knowledge.UNKNOWN, "Cursor question surface is not parsed by this adapter"
            ),
            "permission_request": without(
                Knowledge.UNKNOWN, "Cursor permission surface is not parsed by this adapter"
            ),
            "tool_activity": present(_project_tools(p["tool_activity"])),
            "active_model": present(
                ModelState(active["model_id"], active["effort"], active["display_name"], "cursor")
            )
            if active["model_id"]
            else without(Knowledge.UNKNOWN, "no Cursor active-model readback"),
            "model_configuration": _model_configuration(models, present, without),
            "usage": _usage_observed(evidence, p["terminal_usage"], ref, now, revision),
        }
        events: list[dict[str, object]] = []
        if models["picker"]["visible"]:
            events.append({"type": "cursor.model_picker_visible", "page": models["picker"]["page"]})
        if p["resume_picker"]["visible"]:
            events.append(
                {
                    "type": "cursor.resume_picker_visible",
                    "count": len(p["resume_picker"]["sessions"]),
                }
            )
        return ObservationDelta(
            updates=updates,
            evidence_refs=tuple(entry.ref() for entry in evidence),
            semantic_events=tuple(events),
            diagnostics=frame.diagnostics.messages,
        )

    def lower(
        self, action: SemanticAction, snapshot: ObservationSnapshot
    ) -> Sequence[TerminalEffect]:
        prefix = action.action_id
        if isinstance(action, InsertPromptPayload):
            return tuple(
                SendLiteralKeys(f"{prefix}:type:{index}", chunk.text, FAST_HUMANIZED_TYPING)
                if chunk.provenance is InputProvenance.USER_TYPED
                else PasteBuffer(f"{prefix}:paste:{index}", chunk.text)
                for index, chunk in enumerate(action.chunks)
            )
        if isinstance(action, ClearComposer):
            return (SendNamedKey(f"{prefix}:clear", "C-u"),)
        if isinstance(action, CommitPromptSubmission):
            return (SendNamedKey(f"{prefix}:commit", "Enter"),)
        if isinstance(action, RequestUsage):
            raise ValueError(
                "Cursor usage is authoritative HTTP evidence; terminal RequestUsage is unsupported"
            )
        if isinstance(action, (DismissOverlay, RestoreComposer, SendInterrupt)):
            return (SendNamedKey(f"{prefix}:escape", "Escape"),)
        if isinstance(action, SelectModel):
            return self._lower_model(action, snapshot)
        if isinstance(action, (OpenModelPicker, NavigateModelPicker)):
            return self._lower_model_picker_action(action, snapshot)
        raise ValueError(f"Cursor lowering does not support {type(action).__name__}")

    def _lower_model_picker_action(
        self,
        action: OpenModelPicker | NavigateModelPicker,
        snapshot: ObservationSnapshot,
    ) -> Sequence[TerminalEffect]:
        if isinstance(action, NavigateModelPicker):
            if (
                snapshot.surface.knowledge is not Knowledge.PRESENT
                or snapshot.surface.value is None
                or snapshot.surface.value.primary is not SurfaceKind.MODEL_PICKER
            ):
                raise ValueError("Cursor model-picker navigation requires a visible picker")
            return (
                SendNamedKey(
                    f"{action.action_id}:navigate-model",
                    "Down" if action.direction == "down" else "Up",
                ),
            )
        return self._open_model_picker(action, snapshot)

    def _lower_model(
        self, action: SelectModel, snapshot: ObservationSnapshot
    ) -> Sequence[TerminalEffect]:
        config = snapshot.model_configuration
        if config.knowledge is not Knowledge.PRESENT or config.value is None:
            raise ValueError(
                "Cursor model selection requires current picker configuration evidence"
            )
        if snapshot.surface.knowledge is not Knowledge.PRESENT or snapshot.surface.value is None:
            raise ValueError("Cursor model selection requires a known current surface")
        if snapshot.surface.value.primary is not SurfaceKind.MODEL_PICKER:
            raise ValueError("Cursor model selection will not reopen an unobserved picker")
        target = action.model_id
        parameters = dict(config.value.parameters)
        # The editor is a distinct observed stage.  Parameter selection and
        # configuration confirmation are separate controller actions, never a
        # speculative tail of the model-row selection batch.
        if parameters.get("stage") == "effort":
            return _lower_parameter_selection(action, parameters)
        filter_text = next(
            (
                choice.label
                for choice in config.value.available
                if choice.stable_choice_id == target
            ),
            target.replace("-", " "),
        )
        effects: list[TerminalEffect] = []
        existing_filter = parameters.get("filter_text")
        filter_is_target = bool(
            isinstance(existing_filter, str)
            and existing_filter.casefold() == filter_text.casefold()
        )
        if isinstance(existing_filter, str) and existing_filter and not filter_is_target:
            # Cursor's model-filter editor does not honor the usual readline
            # kill bindings. Its observed caret is at the end of the rendered
            # filter, so clear exactly the observed characters with Backspace.
            effects.extend(
                SendNamedKey(f"{action.action_id}:clear-filter:{index}", "Backspace")
                for index in range(len(existing_filter))
            )
        # Cursor drops filter characters when separate terminal effects arrive
        # back-to-back. One literal-key effect remains character-wise at the
        # transport boundary and supplies an actual inter-character delay.
        if not filter_is_target:
            effects.append(
                SendLiteralKeys(
                    f"{action.action_id}:filter",
                    filter_text,
                    _CURSOR_FILTER_TYPING,
                )
            )
        if action.effort is None:
            effects.append(SendNamedKey(f"{action.action_id}:select", "Enter"))
            return tuple(effects)
        # Cursor selects a row with Enter, but opens its parameter editor with
        # Tab.  Observe that editor before any effort/fast-mode interaction
        # instead of chaining speculative navigation after the picker action.
        effects.append(SendNamedKey(f"{action.action_id}:edit-model", "Tab"))
        return tuple(effects)

    def _open_model_picker(
        self, action: OpenModelPicker, snapshot: ObservationSnapshot
    ) -> Sequence[TerminalEffect]:
        if snapshot.surface.knowledge is not Knowledge.PRESENT or snapshot.surface.value is None:
            raise ValueError("Cursor model picker requires a known safe surface")
        if snapshot.surface.value.primary not in {SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}:
            raise ValueError("Cursor model picker will not replace an unobserved overlay")
        effects: list[TerminalEffect] = []
        composer_text: str | None = None
        if snapshot.composer.knowledge is Knowledge.PRESENT and snapshot.composer.value is not None:
            composer_text = snapshot.composer.value.text
        if composer_text is not None and composer_text.strip().startswith("/model"):
            return (SendNamedKey(f"{action.action_id}:confirm-command", "Enter"),)
        if composer_text:
            effects.append(SendNamedKey(f"{action.action_id}:clear-composer", "C-u"))
        command = "/model"
        if action.filter_text:
            command = f"/model {_model_filter_label(action.filter_text)}"
        effects.append(SendLiteralKeys(f"{action.action_id}:open", command, _CURSOR_FILTER_TYPING))
        if action.filter_text:
            key = "Tab" if action.edit_parameters else "Enter"
            effects.append(SendNamedKey(f"{action.action_id}:open-confirm", key))
        return tuple(effects)


def _raw_frame(frame: TerminalFrame) -> dict[str, object]:
    return {
        "text": frame.raw_text,
        "ansi_preserved": frame.ansi_preserved,
        "width": frame.width,
        "height": frame.height,
        "pane_epoch": frame.pane_epoch,
        "capture_sequence": frame.capture_sequence,
    }


def _composer(clean: str) -> dict[str, object]:
    if (
        "available models" in clean.casefold()
        or _PARAMETER_TITLE.search(clean)
        or _RESUME_TITLE.search(clean)
    ):
        return {
            "visible": False,
            "text": None,
            "normalized_text": None,
            "fingerprint": None,
            "cursor_visible": None,
            "focused": None,
            "partial": None,
            "queued_follow_up": None,
            "attachments": _attachments(clean),
        }
    matches = [(index, _INPUT.match(line)) for index, line in enumerate(clean.splitlines())]
    found = [(index, match) for index, match in matches if match is not None]
    attachments = _attachments(clean)
    if not found:
        return {
            "visible": False,
            "text": None,
            "normalized_text": None,
            "fingerprint": None,
            "cursor_visible": None,
            "focused": None,
            "partial": None,
            "queued_follow_up": None,
            "attachments": attachments,
        }
    _index, match = found[-1]
    raw = match.group("text").strip()
    raw = re.sub(r"\s+ctrl\+c to stop\s*$", "", raw, flags=re.I).rstrip()
    placeholder = raw.casefold() in {"add a follow-up", "plan, search, build anything"}
    text = "" if placeholder else raw
    normalized = re.sub(r"\s+", " ", text).strip()
    busy = bool(_BUSY.search(_live_tail(clean)))
    return {
        "visible": True,
        "text": text,
        "normalized_text": normalized,
        "fingerprint": _fingerprint(normalized),
        "cursor_visible": True,
        "focused": True,
        "partial": bool(text and busy),
        "queued_follow_up": text if text and busy else None,
        "attachments": attachments,
    }


def _models(clean: str) -> dict[str, object]:
    rows = parse_cursor_model_list(clean, model_id_for_label=_model_id)
    pointer_labels = {
        re.sub(r"^\s*[→>]\s*", "", line).strip().split("  ", 1)[0]
        for line in clean.splitlines()
        if re.match(r"^\s*[→>]\s+", line)
    }
    choices = [
        {
            "model_id": model_id,
            "label": label,
            "highlighted": label in pointer_labels,
            # Cursor's arrow is navigation focus only.  This surface renders no
            # independent marker for the saved/configured or active model.
            "current": None,
            "selected": None,
            "disabled": False,
        }
        for model_id, label in rows
    ]
    page = parse_cursor_model_page(clean)
    filter_match = re.search(r"^\s*Filter:\s*(?P<text>.*)$", clean, re.I | re.M)
    param_title = _PARAMETER_TITLE.search(clean)
    params, parameter_options = _parameters(clean)
    if page is not None:
        params.extend(
            (
                ("model_page_start", str(page[0])),
                ("model_page_end", str(page[1])),
                ("model_page_total", str(page[2])),
            )
        )
    active = _active_model(clean)
    return {
        "picker": {
            "visible": bool(rows) and page is not None,
            "filter_text": filter_match.group("text").strip() if filter_match else None,
            "page": page,
            "choices": choices,
        },
        "parameters": {
            "visible": param_title is not None,
            "model_label": param_title.group("model").strip() if param_title else None,
            "values": params,
            "options": parameter_options,
        },
        "active_readback": active,
    }


def _parameters(  # noqa: PLR0912 -- Cursor renders several parameter row grammars
    clean: str,
) -> tuple[
    list[tuple[str, str | bool | None]],
    dict[str, tuple[dict[str, str | bool], ...]],
]:
    values: list[tuple[str, str | bool | None]] = []
    options: dict[str, list[dict[str, str | bool]]] = {}
    parameter_title = _PARAMETER_TITLE.search(clean)
    if parameter_title is not None:
        values.append(("stage", "effort"))
        model_id = _model_id(parameter_title.group("model"))
        if model_id is not None:
            values.append(("configured_model_id", model_id))
    for match in _CHECKED.finditer(clean):
        name = match.group("name").strip().casefold()
        if name in {"fast", "thinking"}:
            parameter_name = "fast_enabled" if name == "fast" else name
            values.append((parameter_name, match.group("mark").strip().casefold() in {"x", "✓"}))
    active_section: str | None = None
    for line in clean.splitlines():
        stripped = line.strip()
        if stripped.casefold() in {"context", "effort"}:
            active_section = stripped.casefold()
            continue
        option_match = _OPTION.match(line)
        if option_match and active_section:
            label = (
                normalize_effort(option_match.group("label"))
                if active_section == "effort"
                else option_match.group("label")
            )
            if label is None:
                continue
            current = bool(
                option_match.group("current") or option_match.group("radio") in {"●", "◉"}
            )
            options.setdefault(active_section, []).append(
                {
                    "label": label,
                    "highlighted": bool(option_match.group("pointer")),
                    "current": current,
                }
            )
            if current:
                values.append((active_section, label))
    for section, rows in options.items():
        for index, row in enumerate(rows):
            values.append((f"{section}_option.{row['label']}", str(index)))
            if row["highlighted"]:
                values.append((f"{section}_highlighted_index", str(index)))
    fast = _FAST_RADIO.search(clean)
    if fast is not None:
        values.append(("fast_enabled", fast.group("radio") in {"●", "◉"}))
    return values, {name: tuple(rows) for name, rows in options.items()}


def _active_model(clean: str) -> dict[str, str | None]:
    for line in reversed(_live_tail(clean).splitlines()):
        match = _STATUS.match(line)
        if match is None or "available models" in line.casefold():
            continue
        label = re.sub(r"\s+·\s+\d+(?:\.\d+)?%\s*$", "", match.group("label")).strip()
        speed = re.search(r"\b(Slow|Fast)\s*$", label, re.I)
        if speed is not None:
            label = label[: speed.start()].strip()
        effort_match = re.search(r"\b(Extra High|Low|Medium|High|Max)\s*$", label, re.I)
        effort = normalize_effort(effort_match.group(1)) if effort_match is not None else None
        if effort_match is not None:
            label = label[: effort_match.start()].strip()
        label = re.sub(r"\s+\d+[KMG]\s*$", "", label, flags=re.I).strip()
        label = re.sub(r"\s+\(Thinking\)\s*$", "", label, flags=re.I).strip()
        model_id = _model_id(label)
        if model_id:
            if model_id.startswith("composer"):
                effort = normalize_effort(speed.group(1)) if speed is not None else "slow"
            return {
                "model_id": model_id,
                "display_name": label,
                "effort": effort,
            }
    return {"model_id": None, "display_name": None, "effort": None}


def _workspace(clean: str) -> dict[str, str | None]:
    for line in reversed(clean.splitlines()):
        match = _WORKSPACE.match(line)
        if match:
            return {"path": match.group("path").strip(), "branch": match.group("branch")}
    return {"path": None, "branch": None}


def _context_evidence(clean: str) -> dict[str, object]:
    # Retain all context-labelled rendered lines even though Cursor's exact
    # context panel grammar is not fixture-backed yet.
    lines = tuple(line.strip() for line in clean.splitlines() if "context" in line.casefold())
    rendered = "\n".join(lines)
    return {
        "lines": lines,
        "recognized": bool(lines),
        "token_estimates": tuple(
            int(match.group("count")) for match in _TOKEN_COUNT.finditer(rendered)
        ),
        "percentages": tuple(
            float(match.group("percent"))
            for match in re.finditer(r"(?P<percent>\d+(?:\.\d+)?)%", rendered)
        ),
    }


def _resume_picker(clean: str) -> dict[str, object]:
    if not _RESUME_TITLE.search(clean):
        return {"visible": False, "sessions": (), "pagination": None}
    sessions = []
    for line in clean.splitlines():
        match = re.match(
            r"^\s*(?P<pointer>→)?\s*(?P<title>.+?)\s{2,}(?P<created>\S.*?)(?:\s{2,}(?P<updated>\S.*))?$",
            line,
        )
        if match and match.group("title").strip() not in {
            "Previous Sessions",
            "Created",
            "Last updated",
        }:
            sessions.append(
                {
                    "title": match.group("title").strip(),
                    "created": match.group("created").strip(),
                    "updated": (match.group("updated") or "").strip(),
                    "highlighted": bool(match.group("pointer")),
                }
            )
    page = re.search(r"\b(?P<current>\d+)\s*/\s*(?P<total>\d+)\b", clean)
    return {
        "visible": True,
        "sessions": tuple(sessions),
        "pagination": page.groupdict() if page else None,
    }


def _tool_activity(clean: str, transcript: Mapping[str, Any]) -> dict[str, object]:
    commands = tuple(match.group("command").strip() for match in _SHELL.finditer(clean))
    reads = tuple(sorted(set(match.group("path") for match in _READ.finditer(clean))))
    writes = tuple(sorted(set(match.group("path") for match in _WRITE.finditer(clean))))
    segments = transcript.get("segments", []) if isinstance(transcript, Mapping) else []
    tools = [
        segment
        for segment in segments
        if isinstance(segment, Mapping) and segment.get("type") == "tool_call"
    ]
    busy = bool(_BUSY.search(_live_tail(clean)))
    return {
        "busy": busy,
        "commands": commands,
        "paths_read": reads,
        "paths_written": writes,
        "transcript_tools": tuple(dict(tool) for tool in tools),
        "status": "running" if busy else "complete",
    }


def _terminal_usage(clean: str) -> dict[str, object]:
    active = _active_model(clean)
    match = re.search(r"·\s*(?P<percent>\d+(?:\.\d+)?)%", clean)
    tokens = _TOKEN_COUNT.search(clean)
    return {
        "context_percent": float(match.group("percent")) if match else None,
        "token_count": int(tokens.group("count")) if tokens else None,
        "active_model": active["model_id"],
        "source": "cursor-terminal-status",
    }


def _notices(clean: str) -> tuple[str, ...]:
    return tuple(
        line.strip()
        for line in clean.splitlines()
        if re.search(r"\b(?:warning|error|trust|required|login)\b", line, re.I)
    )


def _generation(activity: Mapping[str, Any], usage: Mapping[str, Any]) -> GenerationState:
    busy = bool(activity["busy"])
    return GenerationState(
        GenerationPhase.RUNNING_TOOL if busy else GenerationPhase.IDLE,
        busy,
        busy,
        None,
        usage.get("token_count"),
        str(activity["commands"][-1]) if activity.get("commands") else None,
    )


def _tail(transcript: Mapping[str, Any], busy: bool) -> TranscriptTailState:
    segments = transcript.get("segments", []) if isinstance(transcript, Mapping) else []
    users = [
        segment
        for segment in segments
        if isinstance(segment, Mapping) and segment.get("type") == "user"
    ]
    assistants = [
        segment
        for segment in segments
        if isinstance(segment, Mapping) and segment.get("type") == "assistant"
    ]
    user = users[-1] if users else None
    assistant = assistants[-1] if assistants else None
    user_text = str(user.get("text", "")) if user else ""
    assistant_text = str(assistant.get("text", "")) if assistant else ""
    return TranscriptTailState(
        TurnRef(_fingerprint(user_text)[:16], "user") if user else None,
        TurnRef(_fingerprint(assistant_text)[:16], "assistant") if assistant else None,
        tuple(_fingerprint(str(item.get("text", ""))) for item in users),
        busy,
        bool(assistant and assistant.get("phase") == "final" and not busy),
        _fingerprint(assistant_text or user_text) if (assistant_text or user_text) else None,
        len(segments),
    )


def _project_tools(payload: Mapping[str, Any]) -> ToolActivityState:
    commands = list(payload.get("commands", ()))
    if not commands:
        commands.extend(
            str(tool.get("title") or tool.get("input") or "Cursor tool")
            for tool in payload.get("transcript_tools", ())
            if isinstance(tool, Mapping)
        )
    interactions = tuple(
        ToolInteraction(
            "shell",
            command,
            tuple(payload["paths_read"]),
            tuple(payload["paths_written"]),
            str(payload["status"]),
            None,
            None,
        )
        for command in commands
    )
    return ToolActivityState(
        interactions if payload.get("busy") else (), interactions if not payload.get("busy") else ()
    )


def _model_configuration(models: Mapping[str, Any], present: Any, without: Any) -> Observed[object]:
    picker, parameters = models["picker"], models["parameters"]
    if not picker["visible"] and not parameters["visible"]:
        return without(Knowledge.UNKNOWN, "no Cursor model picker or parameter editor visible")
    # Visible rows remain available to exhaustive discovery. Page metadata in
    # ``parameters`` lets selection distinguish viewport absence from a known
    # exhaustive absence without throwing these rows away.
    choices = tuple(
        ChoiceState(
            row["model_id"],
            row["label"],
            selected=row["selected"],
            highlighted=row["highlighted"],
            current=row["current"],
            disabled=row["disabled"],
        )
        for row in picker["choices"]
    )
    highlighted = next((row["model_id"] for row in picker["choices"] if row["highlighted"]), None)
    selected = next((row["model_id"] for row in picker["choices"] if row["selected"]), None)
    parameter_values = tuple(parameters["values"])
    if picker["filter_text"] is not None:
        parameter_values += (("filter_text", picker["filter_text"]),)
    parameter_map = dict(parameter_values)
    configured = next((row["model_id"] for row in picker["choices"] if row["current"]), None)
    if configured is None and isinstance(parameter_map.get("configured_model_id"), str):
        configured = parameter_map["configured_model_id"]
    return present(
        ModelConfigurationState(
            choices,
            highlighted,
            selected,
            configured,
            True if parameters["visible"] else None,
            parameter_values,
        )
    )


def _attachments(clean: str) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "label": match.group("label"),
            "kind": "pasted_text",
            "ordinal": int(match.group("ordinal")),
            "line_count": int(match.group("lines")) if match.group("lines") else None,
        }
        for match in _ATTACHMENT.finditer(clean)
    )


def _live_tail(clean: str, *, lines: int = 24) -> str:
    """Restrict live control claims to Cursor's current rendered chrome."""
    visible = clean.splitlines()
    while visible and not visible[-1].strip():
        visible.pop()
    return "\n".join(visible[-lines:])


def _lower_parameter_selection(
    action: SelectModel, parameters: Mapping[str, object]
) -> Sequence[TerminalEffect]:
    """Navigate a current Cursor editor by observed semantic option identity."""
    effects: list[TerminalEffect] = []
    if action.effort is not None:
        target = parameters.get(f"effort_option.{action.effort}")
        highlighted = parameters.get("effort_highlighted_index")
        if not isinstance(target, str) or not target.isdigit():
            raise ValueError("requested Cursor effort is absent from observed parameter editor")
        if not isinstance(highlighted, str) or not highlighted.isdigit():
            raise ValueError("Cursor parameter editor has no observed highlighted effort")
        offset = int(target) - int(highlighted)
        key = "Down" if offset > 0 else "Up"
        effects.extend(
            SendNamedKey(f"{action.action_id}:effort-{index}", key) for index in range(abs(offset))
        )
        effects.append(SendNamedKey(f"{action.action_id}:select-effort", "Enter"))
    if action.fast_enabled is not None:
        current_fast = parameters.get("fast_enabled")
        if not isinstance(current_fast, bool):
            raise ValueError("Cursor parameter editor has no observed Fast toggle")
        if current_fast != action.fast_enabled:
            effects.append(SendNamedKey(f"{action.action_id}:toggle-fast", "Enter"))
    if not effects:
        effects.append(SendNamedKey(f"{action.action_id}:confirm-configuration", "Enter"))
    return tuple(effects)


def _usage_observed(
    evidence: Sequence[EvidenceEnvelope],
    terminal: Mapping[str, Any],
    ref: object,
    now: datetime,
    revision: ObservationRevision,
) -> Observed[object]:
    http = next(
        (entry for entry in reversed(evidence) if entry.evidence_type == "cursor.http_usage.v1"),
        None,
    )
    if http is not None:
        status = http.payload.get("status", {})
        windows = (
            tuple(
                UsageWindow(
                    str(row.get("name") or "usage"),
                    row.get("percent_used"),
                    None,
                    row.get("reset_at"),
                )
                for row in status.get("windows", [])
                if isinstance(row, Mapping)
            )
            if isinstance(status, Mapping)
            else ()
        )
        return Observed.present(
            UsageState(
                None,
                status.get("plan") if isinstance(status, Mapping) else None,
                windows,
                "CURRENT",
                None,
                None,
                status.get("raw") if isinstance(status, Mapping) else None,
            ),
            evidence=(http.ref(), ref),
            observed_at=now,
            revision=revision,
            explanation="Cursor HTTP usage is authoritative; terminal status remains evidence",
        )
    return Observed.without_value(
        Knowledge.UNKNOWN,
        evidence=(ref,),
        observed_at=now,
        revision=revision,
        explanation=(
            "Cursor terminal percentage is retained as context evidence; "
            "no authoritative HTTP usage supplied"
        ),
    )


__all__ = ["CursorHarnessAdapter"]
