"""Pi's broad evidence edge and pure terminal-effect lowering."""

# ruff: noqa: PLR0911

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import datetime
from uuid import uuid4

from murder.llm.harness_control.adapters.base import HarnessActionAdapter, HarnessObservationAdapter
from murder.llm.harness_control.model.actions import (
    FAST_HUMANIZED_TYPING,
    ClearComposer,
    CommitPromptSubmission,
    DismissOverlay,
    InputProvenance,
    InsertPromptPayload,
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
    EvidenceRef,
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
)
from murder.llm.harnesses.parsing import normalize_effort, strip_ansi
from murder.llm.harnesses.transcripts import parse_frames

_ACTIVE = re.compile(
    r"\((?P<provider>[a-z][a-z0-9_-]*)\)\s+(?P<model>[A-Za-z0-9][A-Za-z0-9._+-]*)(?:\s*[•·]\s*(?P<effort>low|medium|high))?",
    re.I,
)
_FOOTER = re.compile(r"(?P<context>\d+(?:\.\d+)?%/\d+(?:\.\d+)?[kKmM])\s*\((?P<mode>[^)]*)\)")
_RESUME = re.compile(r"Resume Session \((?P<scope>[^)]+)\)", re.I)
_COMPACTION = re.compile(r"(?:\[compaction\]|compacted\s+from|compacting)", re.I)
_UPDATE = re.compile(r"(?:update available|new version|changelog)", re.I)
_WARNING = re.compile(r"(?:warning:|extended-keys|no session found)", re.I)
_BUSY = re.compile(
    r"(?:thinking|streaming|running|working|executing|tool calls?|retrying|compacting)", re.I
)
_SPINNER = re.compile(r"(?:[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s*)?(?:working|thinking)\.\.\.", re.I)
_RULE = re.compile(r"^\s*[─━-]{20,}\s*$")
_COMMAND = re.compile(
    r"^\s*(?P<highlighted>[→>])?\s*(?P<command>/[\w-]+)(?:\s{2,}(?P<description>.+))?\s*$"
)
_RESUME_FILTER = re.compile(r"^\s*>\s*(?P<filter>.*)$")
_RESUME_SETTINGS = re.compile(
    r"(?P<scope_current>[◉●])\s*Current Folder\s*\|\s*(?P<scope_all>[○◯])\s*All\s+"
    r"Name:\s*(?P<name>[^\s]+)\s+Sort:\s*(?P<sort>[^\n]+)",
    re.I,
)
_SHELL_PROMPT = re.compile(r"^\s*(?P<prompt>[^\s@]+@[^\s:]+:.*?[$%#])\s*(?P<command>.*)$")
_TOOL = re.compile(
    r"^\s*(?P<name>[A-Za-z_][\w.-]*)\s*(?:\((?P<args>[^)]*)\)|:\s*(?P<detail>.+))\s*$"
)
_AGENT = re.compile(r"\b(?P<count>\d+)\s+(?:sub)?agents?\b", re.I)
_TOKEN_OR_COST = re.compile(
    r"\b(?:(?P<tokens>[\d,]+)\s+tokens?|\$(?P<cost>\d+(?:\.\d+)?)(?:\s+cost)?)\b", re.I
)


def _present(
    value: object, ref: EvidenceRef, at: datetime, rev: ObservationRevision
) -> Observed[object]:
    return Observed.present(value, evidence=(ref,), observed_at=at, revision=rev)


def _without(
    state: Knowledge, ref: EvidenceRef, at: datetime, rev: ObservationRevision
) -> Observed[object]:
    return Observed.without_value(state, evidence=(ref,), observed_at=at, revision=rev)


def _model_rows(clean: str) -> list[dict[str, object]]:
    rows = []
    for line in clean.splitlines():
        match = re.match(
            r"\s*(?P<cursor>→)?\s*(?P<id>[\w.-]+/[\w.-]+|[\w.-]+\.(?:gguf|bin))\s*(?:\[(?P<source>[^]]+)\])?\s*(?P<current>✓)?",
            line,
            re.I,
        )
        if match and ("/" in match.group("id") or match.group("id").endswith((".gguf", ".bin"))):
            rows.append(
                {
                    "stable_choice_id": match.group("id"),
                    "label": match.group("id"),
                    "selected": bool(match.group("cursor")),
                    "highlighted": bool(match.group("cursor")),
                    "current": bool(match.group("current")),
                    "disabled": None,
                    "source": match.group("source"),
                }
            )
    return rows


def _composer_evidence(lines: list[str], *, occluded: bool) -> dict[str, object]:
    """Recover Pi's bottom composer only from its two explicit divider rules."""

    if occluded:
        return {"visible": False, "reason": "modal replaces the composer region"}
    rule_indices = [index for index, line in enumerate(lines) if _RULE.match(line)]
    for start, end in zip(reversed(rule_indices[:-1]), reversed(rule_indices[1:]), strict=False):
        if end <= start:
            continue
        following = lines[end + 1 : end + 3]
        if not any("/" in line or re.search(r"\([^)]*\)\s+\S+", line) for line in following):
            continue
        text = "\n".join(lines[start + 1 : end]).strip("\n")
        normalized = " ".join(text.split())
        return {
            "visible": True,
            "text": text,
            "normalized_text": normalized,
            "fingerprint": hashlib.sha256(normalized.encode()).hexdigest(),
            "partial": False,
        }
    return {"visible": False, "reason": "composer dividers were not both visible"}


class PiHarnessAdapter(HarnessObservationAdapter, HarnessActionAdapter):
    """Pi's ingestion edge.

    The payload deliberately retains Pi-only picker, startup, footer, shell,
    transcript, context, and agent details.  Only control-relevant concepts are
    projected below; nothing is discarded merely because a controller has not
    acquired a shared vocabulary for it yet.
    """

    parser_version = "pi-evidence/v3"

    def parse_evidence(
        self, frame: TerminalFrame, history: Sequence[EvidenceEnvelope]
    ) -> Sequence[EvidenceEnvelope]:
        del history
        clean = strip_ansi(frame.raw_text)
        lines = clean.splitlines()
        active_match = next((match for match in reversed(list(_ACTIVE.finditer(clean)))), None)
        active = (
            None
            if active_match is None
            else {
                "provider": active_match.group("provider"),
                "model_id": f"{active_match.group('provider')}/{active_match.group('model')}",
                "effort": normalize_effort(active_match.group("effort")),
                "display_name": active_match.group("model"),
            }
        )
        rows = _model_rows(clean)
        display_name = next(
            (
                line.split(":", 1)[1].strip()
                for line in lines
                if line.strip().casefold().startswith("model name:")
            ),
            None,
        )
        highlighted_rows = [row for row in rows if row.get("highlighted")]
        if display_name and len(highlighted_rows) == 1:
            highlighted_rows[0]["display_name"] = display_name
        footer_match = next(iter(reversed(list(_FOOTER.finditer(clean)))), None)
        footer = footer_match.groupdict() if footer_match is not None else None
        if footer is not None:
            footer["context_usage"] = _context_usage(footer["context"], footer["mode"])
        resume = _RESUME.search(clean)
        resume_settings = _RESUME_SETTINGS.search(clean)
        command_choices = _command_autocomplete(lines)
        diagnostics: list[str] = []
        try:
            transcript = parse_frames("pi", [clean])
        except Exception as exc:  # Broad evidence must survive transcript parser defects.
            transcript = {"harness": "pi", "state": "unknown", "segments": []}
            diagnostics.append(f"transcript parse failed: {type(exc).__name__}: {exc}")
        composer = _composer_evidence(lines, occluded=bool(rows) or resume is not None)
        interrupted = "Operation aborted" in clean
        spinner_lines = [line.strip() for line in lines if _SPINNER.search(line)]
        payload: dict[str, object] = {
            "raw_frame": {
                "text": frame.raw_text,
                "ansi_preserved": frame.ansi_preserved,
                "width": frame.width,
                "height": frame.height,
                "pane_epoch": frame.pane_epoch,
                "capture_sequence": frame.capture_sequence,
            },
            "command_autocomplete": {
                "visible": bool(command_choices),
                "choices": command_choices,
                "filter_text": _filter_text(lines, command_choices),
            },
            "model_scope": _model_scope(lines),
            "model_picker": {
                "scope": next(
                    (line.strip() for line in lines if line.strip().startswith("Scope:")), None
                ),
                "rows": rows,
                "filter_text": _filter_text(lines, rows),
                "visible": bool(rows),
                "configured_model_id": next(
                    (str(row["stable_choice_id"]) for row in rows if row.get("current")), None
                ),
                "pending_changes": False if rows else None,
            },
            "resume": {
                "visible": resume is not None,
                "scope": resume.group("scope") if resume else None,
                "filter_text": _resume_filter(lines) if resume else None,
                "scope_selection": (
                    "current_folder"
                    if resume_settings and resume_settings.group("scope_current")
                    else None
                ),
                "scope_options": ("current_folder", "all") if resume_settings else (),
                "name_mode": resume_settings.group("name") if resume_settings else None,
                "sort_mode": resume_settings.group("sort").strip() if resume_settings else None,
                "controls": [line.strip() for line in lines if "ctrl+" in line.lower()],
                "empty": "No sessions" in clean,
            },
            "active_model": active,
            "composer": composer,
            "footer": {
                **(footer or {}),
                "metrics": _footer_metrics(lines),
                "active_provider": active.get("provider") if active else None,
                "active_model_id": active.get("model_id") if active else None,
                "active_effort": active.get("effort") if active else None,
            },
            "startup_warnings": _notices(lines, _WARNING),
            "update_notices": _notices(lines, _UPDATE),
            "compaction": {
                "visible": bool(_COMPACTION.search(clean)),
                "lines": [line.strip() for line in lines if _COMPACTION.search(line)],
            },
            "generation": {
                "phase": "stopped"
                if interrupted
                else "compacting"
                if _COMPACTION.search(clean)
                else "streaming"
                if spinner_lines
                else "idle",
                "active": bool(spinner_lines) and not interrupted,
                "spinner_visible": bool(spinner_lines),
                "interrupted": interrupted,
            },
            "shell": _shell_evidence(lines),
            "transcript": transcript,
            "status_lines": [line.strip() for line in lines if _BUSY.search(line)],
            "context": _context_evidence(lines, footer),
            "subagents": _subagent_evidence(lines),
            "tool_activity": _tool_evidence(transcript, lines),
        }
        return (
            EvidenceEnvelope(
                evidence_id=f"{frame.frame_id}:pi:frame:v3",
                frame_id=frame.frame_id,
                harness_id=frame.harness_id,
                parser_version=self.parser_version,
                captured_at=frame.captured_at,
                evidence_type="pi.frame.v3",
                payload=payload,
                source_regions=(ScreenRegionRef("frame"),),
                diagnostics=EvidenceDiagnostics(
                    parser_name="pi",
                    messages=(
                        "Pi-specific command/model/resume/startup/shell/footer/context/"
                        "transcript/tool/agent evidence retained",
                        *diagnostics,
                    ),
                ),
            ),
        )

    def project_observations(
        self, evidence: Sequence[EvidenceEnvelope], prior: ObservationSnapshot | None
    ) -> ObservationDelta:
        if not evidence:
            return ObservationDelta(updates={}, diagnostics=("no Pi evidence",))
        item = next(
            (entry for entry in reversed(evidence) if entry.evidence_type == "pi.frame.v3"), None
        )
        if item is None:
            return ObservationDelta(updates={}, diagnostics=("no Pi frame evidence",))
        payload, ref = item.payload, item.ref()
        raw = payload["raw_frame"]
        at, rev = (
            item.captured_at,
            ObservationRevision(
                int(raw["pane_epoch"]),
                int(raw["capture_sequence"]),
                prior.revision.semantic_sequence + 1 if prior else 1,
            ),
        )
        resume, picker = payload.get("resume", {}), payload.get("model_picker", {})
        if isinstance(resume, dict) and resume.get("visible"):
            primary, modal = SurfaceKind.RESUME_PICKER, ModalKind.RESUME
        elif isinstance(picker, dict) and picker.get("rows"):
            primary, modal = SurfaceKind.MODEL_PICKER, ModalKind.MODEL_PICKER
        else:
            primary, modal = SurfaceKind.COMPOSER, None
        active = payload.get("active_model")
        rows = picker.get("rows", []) if isinstance(picker, dict) else []
        choices = tuple(
            ChoiceState(
                row.get("stable_choice_id"),
                str(row.get("display_name") or row.get("label")),
                selected=row.get("selected"),
                highlighted=row.get("highlighted"),
                current=row.get("current"),
                disabled=row.get("disabled"),
            )
            for row in rows
            if isinstance(row, dict)
        )
        composer = payload.get("composer", {})
        generation = payload.get("generation", {})
        transcript = payload.get("transcript", {})
        segments = transcript.get("segments", []) if isinstance(transcript, dict) else []
        users, assistants = (
            [s for s in segments if isinstance(s, dict) and s.get("type") == "user"],
            [s for s in segments if isinstance(s, dict) and s.get("type") == "assistant"],
        )
        user, assistant = (users[-1] if users else None), (assistants[-1] if assistants else None)
        user_text, assistant_text = (
            (str(user.get("text", "")) if user else ""),
            (str(assistant.get("text", "")) if assistant else ""),
        )
        updates: dict[str, Observed[object]] = {
            "surface": _present(
                SurfaceState(
                    primary,
                    frozenset({primary, SurfaceKind.TRANSCRIPT}),
                    primary,
                    primary is not SurfaceKind.COMPOSER,
                    primary is not SurfaceKind.COMPOSER,
                ),
                ref,
                at,
                rev,
            ),
            "modal": _present(
                ModalState(
                    modal,
                    "Resume Session" if modal is ModalKind.RESUME else "Model Picker",
                    None,
                    len(choices),
                    True,
                    True,
                ),
                ref,
                at,
                rev,
            )
            if modal
            else _without(Knowledge.ABSENT, ref, at, rev),
            "composer": _present(
                ComposerState(
                    str(composer.get("text", "")),
                    str(composer.get("normalized_text", "")),
                    str(composer.get("fingerprint")),
                    None,
                    None,
                    ComposerActionability.ACTIONABLE
                    if not isinstance(generation, dict) or not generation.get("active")
                    else ComposerActionability.VISIBLE_NOT_ACTIONABLE,
                    bool(composer.get("partial", False)),
                    not isinstance(generation, dict) or not generation.get("active"),
                ),
                ref,
                at,
                rev,
            )
            if isinstance(composer, dict) and composer.get("visible")
            else _without(Knowledge.UNKNOWN, ref, at, rev),
            "generation": _present(
                GenerationState(
                    {
                        "stopped": GenerationPhase.STOPPED,
                        "compacting": GenerationPhase.COMPACTING,
                        "streaming": GenerationPhase.STREAMING,
                        "idle": GenerationPhase.IDLE,
                    }.get(
                        str(generation.get("phase")) if isinstance(generation, dict) else "unknown",
                        GenerationPhase.UNKNOWN,
                    ),
                    bool(generation.get("active")) if isinstance(generation, dict) else None,
                    bool(generation.get("spinner_visible"))
                    if isinstance(generation, dict)
                    else None,
                    None,
                    None,
                    None,
                    compaction_state="visible"
                    if isinstance(payload.get("compaction"), dict)
                    and payload["compaction"].get("visible")
                    else None,
                ),
                ref,
                at,
                rev,
            ),
            "transcript_tail": _present(
                TranscriptTailState(
                    TurnRef(hashlib.sha256(user_text.encode()).hexdigest()[:16], "user")
                    if user
                    else None,
                    TurnRef(hashlib.sha256(assistant_text.encode()).hexdigest()[:16], "assistant")
                    if assistant
                    else None,
                    tuple(
                        hashlib.sha256(str(s.get("text", "")).encode()).hexdigest() for s in users
                    ),
                    bool(assistant and assistant.get("phase") != "final"),
                    bool(assistant and assistant.get("phase") == "final"),
                    hashlib.sha256((assistant_text or user_text).encode()).hexdigest()
                    if (assistant_text or user_text)
                    else None,
                    len(segments),
                ),
                ref,
                at,
                rev,
            ),
            "active_model": _present(
                ModelState(
                    active["model_id"],
                    active.get("effort"),
                    active.get("display_name"),
                    active.get("provider"),
                ),
                ref,
                at,
                rev,
            )
            if isinstance(active, dict) and active.get("model_id")
            else _without(Knowledge.UNKNOWN, ref, at, rev),
            "model_configuration": _present(
                ModelConfigurationState(
                    choices,
                    next(
                        (choice.stable_choice_id for choice in choices if choice.highlighted), None
                    ),
                    next((choice.stable_choice_id for choice in choices if choice.selected), None),
                    picker.get("configured_model_id") if isinstance(picker, dict) else None,
                    picker.get("pending_changes") if isinstance(picker, dict) else None,
                    tuple(
                        (name, value)
                        for name, value in (
                            (
                                "scope",
                                payload.get("model_scope", {}).get("selected")
                                if isinstance(payload.get("model_scope"), dict)
                                else None,
                            ),
                            ("effort", active.get("effort") if isinstance(active, dict) else None),
                        )
                        if value is not None
                    ),
                ),
                ref,
                at,
                rev,
            )
            if choices
            else _without(Knowledge.UNKNOWN, ref, at, rev),
            "tool_activity": _present(
                _tool_activity(segments, payload.get("tool_activity")), ref, at, rev
            ),
        }
        return ObservationDelta(
            updates=updates,
            evidence_refs=(ref,),
            semantic_events=_events(payload),
            diagnostics=item.diagnostics.messages,
        )

    def lower(
        self, action: SemanticAction, snapshot: ObservationSnapshot
    ) -> Sequence[TerminalEffect]:  # noqa: PLR0911
        if isinstance(action, InsertPromptPayload):
            return tuple(
                SendLiteralKeys(str(uuid4()), part.text, FAST_HUMANIZED_TYPING)
                if part.provenance is InputProvenance.USER_TYPED
                else PasteBuffer(str(uuid4()), part.text)
                for part in action.chunks
            )
        if isinstance(action, ClearComposer):
            return (SendNamedKey(str(uuid4()), "C-u"),)
        if isinstance(action, CommitPromptSubmission):
            return (SendNamedKey(str(uuid4()), "Enter"),)
        if isinstance(action, RequestUsage):
            raise ValueError("Pi has no fixture-backed terminal usage surface")
        if isinstance(action, SelectModel):
            if snapshot.model_configuration.knowledge is not Knowledge.PRESENT:
                raise ValueError(
                    "Pi model selection requires current picker configuration evidence"
                )
            choices = snapshot.model_configuration.value.available
            choice = next(
                (row for row in choices if action.model_id in {row.stable_choice_id, row.label}),
                None,
            )
            if choice is None or choice.disabled is True:
                raise ValueError("Pi target model is not available in the current picker evidence")
            return (
                SendLiteralKeys(str(uuid4()), choice.stable_choice_id or choice.label),
                SendNamedKey(str(uuid4()), "Enter"),
            )
        if isinstance(action, OpenModelPicker):
            if (
                snapshot.surface.knowledge is not Knowledge.PRESENT
                or snapshot.surface.value is None
            ):
                raise ValueError("Pi model picker requires a known safe surface")
            if snapshot.surface.value.primary not in {SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}:
                raise ValueError("Pi model picker will not replace an unobserved overlay")
            return (
                SendLiteralKeys(str(uuid4()), "/model"),
                SendNamedKey(str(uuid4()), "Enter"),
            )
        if isinstance(action, (DismissOverlay, RestoreComposer)):
            return (SendNamedKey(str(uuid4()), "Escape"),)
        if isinstance(action, SendInterrupt):
            return (SendNamedKey(str(uuid4()), "Escape"),)
        return ()


__all__ = ["PiHarnessAdapter"]


def _tool_activity(segments: list[object], evidence: object = None) -> ToolActivityState:
    """Promote explicit tool evidence only; a path-shaped prose fragment is not a write."""

    tools = [
        item for item in segments if isinstance(item, dict) and item.get("type") == "tool_call"
    ]
    if isinstance(evidence, dict):
        tools.extend(item for item in evidence.get("declared", ()) if isinstance(item, dict))
    return ToolActivityState(
        (),
        tuple(
            ToolInteraction(
                str(item.get("title")) if item.get("title") else None,
                str(item.get("command")) if item.get("command") else None,
                (),
                (),
                str(item.get("status") or "complete"),
                None,
                None,
            )
            for item in tools[-8:]
        ),
    )


def _context_usage(context: str, mode: str) -> dict[str, object]:
    percent_text, token_text = context.split("/", 1)
    suffix = token_text[-1].casefold() if token_text[-1].isalpha() else ""
    multiplier = {"k": 1_000, "m": 1_000_000}.get(suffix, 1)
    numeric = token_text[:-1] if suffix else token_text
    return {
        "percent_used": float(percent_text.rstrip("%")),
        "context_window_tokens": int(float(numeric) * multiplier),
        "mode": mode.strip(),
        "raw": f"{context} ({mode.strip()})",
    }


def _command_autocomplete(lines: list[str]) -> list[dict[str, object]]:
    """Keep the command menu structure, rather than treating it as transcript text."""

    choices: list[dict[str, object]] = []
    for number, line in enumerate(lines, start=1):
        match = _COMMAND.match(line)
        if match is None or match.group("description") is None:
            continue
        choices.append(
            {
                "stable_choice_id": match.group("command"),
                "label": match.group("command"),
                "description": match.group("description").strip(),
                "number": number,
                "highlighted": bool(match.group("highlighted")),
                "selected": bool(match.group("highlighted")),
                "disabled": False,
            }
        )
    return choices


def _filter_text(lines: list[str], rows: list[object]) -> str | None:
    """A bare ``>`` is a picker search input only when a picker is visible."""

    if not rows:
        return None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(">") and not _COMMAND.match(line):
            return stripped[1:].strip()
    return None


def _resume_filter(lines: list[str]) -> str | None:
    for line in lines:
        match = _RESUME_FILTER.match(line)
        if match:
            return match.group("filter")
    return None


def _model_scope(lines: list[str]) -> dict[str, object]:
    line = next((line.strip() for line in lines if line.strip().startswith("Model scope:")), None)
    if line is None:
        return {"present": False, "selected": None, "available": (), "raw": None}
    raw = line.partition(":")[2].strip()
    raw = re.sub(r"\s*\(Ctrl\+P to cycle\)\s*$", "", raw, flags=re.I)
    # Terminal wrapping is retained in raw evidence; comma-delimited entries
    # on the visible first line are still useful choices.
    available = tuple(part.strip() for part in raw.split(",") if part.strip())
    return {"present": True, "selected": None, "available": available, "raw": line}


def _notices(lines: list[str], pattern: re.Pattern[str]) -> list[dict[str, object]]:
    return [
        {
            "text": line.strip(),
            "line": index,
            "kind": "warning" if pattern is _WARNING else "update",
        }
        for index, line in enumerate(lines, start=1)
        if pattern.search(line)
    ]


def _shell_evidence(lines: list[str]) -> dict[str, object]:
    matches = [match for line in lines if (match := _SHELL_PROMPT.match(line))]
    return {
        "present": bool(matches),
        "prompts": [match.group("prompt") for match in matches],
        "commands": [match.group("command") for match in matches if match.group("command")],
        "cli_errors": [line.strip() for line in lines if "no session found" in line.casefold()],
    }


def _footer_metrics(lines: list[str]) -> list[dict[str, object]]:
    metrics: list[dict[str, object]] = []
    for line_number, line in enumerate(lines, start=1):
        for match in _TOKEN_OR_COST.finditer(line):
            metrics.append(
                {
                    "line": line_number,
                    "tokens": int(match.group("tokens").replace(",", ""))
                    if match.group("tokens")
                    else None,
                    "cost": float(match.group("cost")) if match.group("cost") else None,
                    "raw": match.group(0),
                }
            )
    return metrics


def _context_evidence(lines: list[str], footer: dict[str, object] | None) -> dict[str, object]:
    related = [
        line.strip() for line in lines if re.search(r"\b(context|token|plugin|mcp)\b", line, re.I)
    ]
    return {
        "footer": footer.get("context_usage") if footer else None,
        "lines": related,
        "contributors": [line for line in related if re.search(r"\b(plugin|mcp)\b", line, re.I)],
    }


def _subagent_evidence(lines: list[str]) -> dict[str, object]:
    matches = [match for line in lines if (match := _AGENT.search(line))]
    return {
        "active_count": max((int(match.group("count")) for match in matches), default=None),
        "lines": [line.strip() for line in lines if _AGENT.search(line)],
    }


def _tool_evidence(transcript: object, lines: list[str]) -> dict[str, object]:
    declared = []
    if isinstance(transcript, dict):
        declared.extend(
            segment
            for segment in transcript.get("segments", ())
            if isinstance(segment, dict) and segment.get("type") == "tool_call"
        )
    # A Pi tool row is admitted only where it explicitly has a call-like syntax;
    # arbitrary prose and paths remain raw transcript evidence.
    for line in lines:
        match = _TOOL.match(line)
        if match and (match.group("args") is not None or match.group("detail") is not None):
            name = match.group("name")
            # ``name: prose`` is only a tool claim for a known execution label.
            # Any arbitrary transcript heading remains broad raw evidence rather
            # than becoming a false tool operation.
            is_call = match.group("args") is not None
            is_known_label = name.casefold() in {
                "tool",
                "shell",
                "bash",
                "exec",
                "read_file",
                "write_file",
                "edit_file",
            }
            if not is_call and not is_known_label:
                continue
            declared.append(
                {
                    "type": "tool_call",
                    "title": name,
                    "command": match.group("args") or match.group("detail"),
                    "status": "visible",
                    "raw_line": line.strip(),
                }
            )
    return {"declared": declared}


def _events(payload: dict[str, object]) -> tuple[dict[str, object], ...]:
    events: list[dict[str, object]] = []
    if isinstance(payload.get("compaction"), dict) and payload["compaction"].get("visible"):
        events.append({"type": "pi.compaction"})
    if isinstance(payload.get("resume"), dict) and payload["resume"].get("visible"):
        events.append({"type": "pi.resume_picker_visible", "scope": payload["resume"].get("scope")})
    if isinstance(payload.get("update_notices"), list) and payload["update_notices"]:
        events.append({"type": "pi.update_notice_visible"})
    if (
        isinstance(payload.get("subagents"), dict)
        and payload["subagents"].get("active_count") is not None
    ):
        events.append(
            {"type": "pi.subagent_activity", "active_count": payload["subagents"]["active_count"]}
        )
    return tuple(events)
