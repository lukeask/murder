# ruff: noqa: PLR0911
"""Codex edge adapter: broad evidence, narrow projection, pure lowering."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from murder.llm.harness_control.adapters.base import HarnessActionAdapter, HarnessObservationAdapter
from murder.llm.harness_control.model.actions import (
    FAST_HUMANIZED_TYPING,
    AnswerPermission,
    AnswerQuestion,
    ClearComposer,
    CommitPromptSubmission,
    DismissOverlay,
    InputProvenance,
    InsertPromptPayload,
    OpenModelPicker,
    PasteBuffer,
    QuestionAnswerMode,
    RequestUsage,
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
    PermissionRequestState,
    QuestionState,
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
    parse_numbered_effort_choices,
    parse_numbered_model_choices,
    strip_ansi,
)
from murder.llm.harnesses.transcripts import parse_frames
from murder.llm.harnesses.usage import parse_codex_status_pane

_PROMPT = re.compile(r"^\s*›\s*(?P<text>.*)$")
_MENU = re.compile(r"^\s*[›>]??\s*\d+\.\s+")
_BUSY = re.compile(
    r"(?P<label>[^\n]*?)(?:\((?P<elapsed>\d+m\s*)?(?P<seconds>\d+)s[^)]*esc to interrupt)", re.I
)
_MODEL = re.compile(
    r"\bmodel:\s*(?P<model>[\w.+:/-]+)(?:\s+(?P<effort>low|medium|high|extra\s+high|xhigh))?", re.I
)
_FOOTER = re.compile(
    r"^\s*(?P<model>[\w.+:/-]+)\s+(?P<effort>low|medium|high|extra\s+high|xhigh)\s+·", re.I | re.M
)
_NOTICE = re.compile(r"^\s*[■⚠]\s*(?P<text>.+)$", re.M)
_BACKGROUND = re.compile(r"(?P<count>\d+)\s+background terminals?\s+running", re.I)
_MCP_STARTUP = re.compile(
    r"^(?P<raw>\s*•\s*Starting MCP servers\s*\((?P<started>\d+)/(?P<total>\d+)\):\s*"
    r"(?P<servers>.*?)\s*\((?P<seconds>\d+)s\s*•\s*esc to interrupt\))\s*$",
    re.I | re.M,
)
_NAMESPACED_TOOL = re.compile(r"^(?P<name>[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)+)\s*\(")
_NUMBERED = re.compile(r"^\s*(?P<pointer>[›❯])?\s*(?P<number>\d+)\.\s+(?P<label>.+?)\s*$")
_BOX_TOP = re.compile(r"^\s*[╭┌╔].*[╮┐╗]\s*$")
_BOX_BOTTOM = re.compile(r"^\s*[╰└╚].*[╯┘╝]\s*$")
_BOX_GUTTER = re.compile(r"^\s*[│┃║]\s?(?P<content>.*?)\s*[│┃║]\s*$")
_CODEX_MENU_CONTROLS = re.compile(
    r"^\s*(?:press\s+)?enter\b.*\b(?:esc|escape)\b", re.IGNORECASE
)
_MINIMUM_MENU_CHOICES = 2
_STATUS_HEADING = re.compile(r">_\s*OpenAI Codex\s*\(v(?P<version>[^)]+)\)", re.I)
_STATUS_FIELD = re.compile(
    r"^\s*(?P<label>Model|Directory|Workspace|Permissions|Agents\.md|Account|"
    r"Collaboration mode|Session)\s*:\s*(?P<value>.*?)\s*$",
    re.I,
)
_RESUME_TITLE = "Resume a previous session"
_RESUME_OPTIONS = re.compile(
    r"Filter:\s*\[(?P<filter>[^]]+)]\s*(?P<filter_other>\S+)\s+"
    r"Sort:\s*\[(?P<sort>[^]]+)]\s*(?P<sort_other>\S+)",
    re.I,
)
_RESUME_SESSION = re.compile(
    r"^\s*(?P<highlighted>❯)?\s*(?P<age>\d+\s*[mhdw]\s+ago)\s+(?P<preview>\S.*)$",
    re.I,
)
_RESUME_PAGE = re.compile(
    r"(?P<selected>\d+)\s*/\s*(?P<total>\d+)\s*·\s*(?P<percent>\d+)%"
)
_UPDATE_TITLE = re.compile(
    r"Update available!\s*(?P<current>[\w.-]+)\s*->\s*(?P<available>[\w.-]+)", re.I
)
_INVALID_RESUME = re.compile(r"(?:ERROR:\s*)?No saved session found|EXIT_CODE:\s*[1-9]", re.I)


class CodexHarnessAdapter(HarnessObservationAdapter, HarnessActionAdapter):
    parser_version = "codex-evidence-v5"

    def parse_evidence(
        self, frame: TerminalFrame, history: Sequence[EvidenceEnvelope]
    ) -> Sequence[EvidenceEnvelope]:
        del history
        clean = strip_ansi(frame.raw_text)
        full = ScreenRegionRef("full_frame", 1, max(1, len(clean.splitlines())))
        diagnostics: list[str] = []
        try:
            transcript = parse_frames("codex", [frame.raw_text], pane_height=frame.height)
        except Exception as exc:  # evidence must survive a parser failure
            transcript = {"harness": "codex", "state": "unknown", "segments": []}
            diagnostics.append(f"transcript parse failed: {type(exc).__name__}: {exc}")
        composer = _composer(clean)
        model_picker_visible = "Select Model and Effort" in clean
        reasoning_picker_visible = "Select Reasoning Level" in clean
        model_choices = [
            {
                "number": row.index,
                "model_id": row.model_id,
                "label": row.label,
                "current": row.current,
                "highlighted": _codex_row_is_highlighted(clean, row.index),
            }
            for row in parse_numbered_model_choices(clean)
        ] if model_picker_visible else []
        effort_choices = [
            {"number": row.index, "effort": row.effort, "label": row.label, "current": row.current}
            for row in parse_numbered_effort_choices(clean)
        ] if reasoning_picker_visible else []
        model_readbacks = _model_readbacks(clean)
        status = parse_codex_status_pane(clean, fetched_at=frame.captured_at.isoformat())
        status_evidence = _status_evidence(clean, status)
        resume_surface = _resume_surface(clean)
        update_surface = _update_surface(clean)
        shell_error = _current_shell_error(clean)
        windows = [
            {"name": item.name, "percent_used": item.percent_used, "reset_at": item.reset_at}
            for item in status.windows
        ]
        modal = (
            "model_picker"
            if model_picker_visible or reasoning_picker_visible
            else "resume_picker"
            if resume_surface["present"]
            else "update"
            if update_surface["present"]
            else "shell_error"
            if shell_error
            else "status"
            if status_evidence["present"]
            else None
        )
        question, permission = _structured_surfaces(clean)
        payload: dict[str, Any] = {
            "raw_frame": {
                "text": frame.raw_text,
                "ansi_preserved": frame.ansi_preserved,
                "width": frame.width,
                "height": frame.height,
                "pane_epoch": frame.pane_epoch,
                "capture_sequence": frame.capture_sequence,
            },
            "composer": composer,
            "transcript": transcript,
            "modal": {
                "kind": modal,
                "model_choices": model_choices,
                "effort_choices": effort_choices,
            },
            "model": {
                "readbacks": model_readbacks,
                "available": model_choices,
                "effort_choices": effort_choices,
                "configuration": _codex_model_configuration(
                    model_choices, effort_choices, model_readbacks, clean
                ),
            },
            "status": {
                **status_evidence,
                "usage_windows": windows,
            },
            "notices": [match.group("text").strip() for match in _NOTICE.finditer(clean)],
            "activity": {
                "busy": _busy(clean),
                "background_terminals": _background_count(clean),
                "tools": _tools(transcript),
                "mcp_startup": _mcp_startup(clean),
            },
            # Codex structured question/approval parsing is intentionally not
            # invented from arbitrary numbered text; retain raw text for later.
            "question_surface": question,
            "permission_surface": permission,
            "resume_surface": resume_surface,
            "update_surface": update_surface,
            "shell": {
                "present": shell_error,
                "kind": "invalid_resume" if shell_error else None,
            },
        }
        return (
            EvidenceEnvelope(
                evidence_id=EvidenceId(f"codex:{frame.frame_id}:v4"),
                frame_id=frame.frame_id,
                harness_id=frame.harness_id,
                parser_version=self.parser_version,
                captured_at=frame.captured_at,
                evidence_type="codex.frame.v4",
                payload=payload,
                source_regions=(full,),
                diagnostics=EvidenceDiagnostics(
                    parser_name=self.parser_version, messages=tuple(diagnostics)
                ),
            ),
        )

    def project_observations(  # noqa: PLR0912, PLR0915
        self, evidence: Sequence[EvidenceEnvelope], prior: ObservationSnapshot | None
    ) -> ObservationDelta:
        item = next(
            (entry for entry in reversed(evidence) if entry.evidence_type == "codex.frame.v4"), None
        )
        if item is None:
            return ObservationDelta(updates={}, diagnostics=("no Codex frame evidence",))
        p = item.payload
        revision = ObservationRevision(
            item.payload["raw_frame"]["pane_epoch"],
            _capture_sequence(item),
            (prior.revision.semantic_sequence + 1) if prior else 1,
        )
        ref, now = item.ref(), item.captured_at

        def obs(value: object) -> Observed[object]:
            return Observed.present(value, evidence=(ref,), observed_at=now, revision=revision)

        def unknown(explanation: str) -> Observed[object]:
            return Observed.without_value(
                Knowledge.UNKNOWN,
                evidence=(ref,),
                observed_at=now,
                revision=revision,
                explanation=explanation,
            )

        composer = p["composer"]
        modal = p["modal"]["kind"]
        busy = p["activity"]["busy"]
        if modal == "resume_picker":
            resume = p["resume_surface"]
            pagination = resume.get("pagination") or {}
            surface = SurfaceState(
                SurfaceKind.RESUME_PICKER,
                frozenset({SurfaceKind.RESUME_PICKER}),
                SurfaceKind.RESUME_PICKER,
                True,
                True,
            )
            modal_state = ModalState(
                ModalKind.RESUME,
                resume["title"],
                (
                    pagination["selected_index"] - 1
                    if pagination.get("selected_index") is not None
                    else None
                ),
                pagination.get("total_count"),
                True,
                True,
            )
            composer_observed = unknown("resume picker occludes Codex composer")
        elif modal == "update":
            surface = SurfaceState(
                SurfaceKind.UNKNOWN_OVERLAY,
                frozenset({SurfaceKind.UNKNOWN_OVERLAY}),
                SurfaceKind.UNKNOWN_OVERLAY,
                True,
                True,
            )
            modal_state = ModalState(
                ModalKind.UNKNOWN, "Codex update available", None, None, True, True
            )
            composer_observed = unknown("update overlay occludes Codex composer")
        elif modal == "shell_error":
            surface = SurfaceState(
                SurfaceKind.SHELL,
                frozenset({SurfaceKind.SHELL}),
                SurfaceKind.SHELL,
                True,
                True,
            )
            modal_state = None
            composer_observed = unknown("Codex exited to a shell error")
        elif modal == "model_picker":
            surface = SurfaceState(
                SurfaceKind.MODEL_PICKER,
                frozenset({SurfaceKind.MODEL_PICKER}),
                SurfaceKind.MODEL_PICKER,
                True,
                True,
            )
            modal_state = ModalState(
                ModalKind.MODEL_PICKER,
                "Select Model and Effort",
                None,
                len(p["modal"]["model_choices"]) + len(p["modal"]["effort_choices"]),
                True,
                True,
            )
            composer_observed = unknown("model picker occludes Codex composer")
        elif p["status"]["present"]:
            surface = SurfaceState(
                SurfaceKind.STATUS_PANEL,
                frozenset({SurfaceKind.STATUS_PANEL}),
                SurfaceKind.STATUS_PANEL,
                True,
                True,
            )
            modal_state = ModalState(ModalKind.STATUS, "Codex status", None, None, True, True)
            composer_observed = unknown("status panel occludes Codex composer")
        elif composer["visible"]:
            surface = SurfaceState(
                SurfaceKind.COMPOSER,
                frozenset({SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}),
                SurfaceKind.COMPOSER,
                False,
                False,
            )
            modal_state = None
            composer_observed = (
                obs(
                    ComposerState(
                        text=composer["text"],
                        normalized_text=composer["normalized_text"],
                        content_fingerprint=composer["fingerprint"],
                        cursor_visible=None,
                        focused=None,
                        actionability=(
                            ComposerActionability.ACTIONABLE
                            if not busy
                            else ComposerActionability.VISIBLE_NOT_ACTIONABLE
                        ),
                        is_partial=composer["partial"],
                        accepts_submission=(not busy if composer["visible"] else None),
                    )
                )
            )
        else:
            surface = SurfaceState(
                SurfaceKind.UNKNOWN_OVERLAY,
                frozenset({SurfaceKind.TRANSCRIPT, SurfaceKind.UNKNOWN_OVERLAY}),
                None,
                True,
                True,
            )
            modal_state = None
            composer_observed = unknown("no actionable Codex composer was established")
        updates: dict[str, Observed[object]] = {
            "surface": obs(surface),
            "composer": composer_observed,
            "modal": obs(modal_state)
            if modal_state
            else Observed.without_value(
                Knowledge.ABSENT, evidence=(ref,), observed_at=now, revision=revision
            ),
            "generation": obs(_generation(busy)),
            "transcript_tail": obs(_tail(p["transcript"], busy)),
            "question": _question_observed(
                p["question_surface"],
                ref,
                now,
                revision,
                absent_confirmed=(modal is None and composer["visible"])
                or p["permission_surface"]["present"],
            ),
            "permission_request": _permission_observed(
                p["permission_surface"],
                ref,
                now,
                revision,
                absent_confirmed=(modal is None and composer["visible"])
                or p["question_surface"]["present"],
            ),
            "tool_activity": obs(_tool_activity(p["activity"]["tools"])),
        }
        readbacks = p["model"]["readbacks"]
        if readbacks:
            active = readbacks[-1]
            updates["active_model"] = obs(
                ModelState(
                    active["model_id"], active.get("effort"), active.get("display_name"), "openai"
                )
            )
        else:
            updates["active_model"] = unknown("no active model readback in frame")
        configuration = p["model"]["configuration"]
        updates["model_configuration"] = (
            obs(
                ModelConfigurationState(
                    tuple(
                        ChoiceState(
                            row["model_id"],
                            row["label"],
                            number=row["number"],
                            selected=row["current"],
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
            if configuration["picker_visible"]
            else unknown("no Codex model-picker configuration is visible")
        )
        windows = tuple(
            UsageWindow(row["name"], row["percent_used"], _dt(row["reset_at"]), row["reset_at"])
            for row in p["status"]["usage_windows"]
        )
        updates["usage"] = (
            obs(
                UsageState(
                    None,
                    None,
                    windows,
                    p["status"]["freshness"],
                    SurfaceKind.STATUS_PANEL if windows else None,
                    p["status"]["freshness_advisory"],
                )
            )
            if windows
            else unknown("no Codex status usage visible")
        )
        events: tuple[dict[str, object], ...] = (
            (
                {
                    "type": "codex.model_picker_visible",
                    "stage": configuration["stage"],
                    "configured_model_id": configuration["configured_model_id"],
                    "highlighted_model_id": configuration["highlighted_model_id"],
                },
            )
            if configuration["picker_visible"]
            else ()
        )
        if p["activity"]["mcp_startup"] is not None:
            events += (
                {
                    "type": "codex.mcp_startup_visible",
                    "started_count": p["activity"]["mcp_startup"]["started_count"],
                    "total_count": p["activity"]["mcp_startup"]["total_count"],
                    "server_names": tuple(p["activity"]["mcp_startup"]["server_names"]),
                },
            )
        if p["resume_surface"]["present"]:
            pagination = p["resume_surface"].get("pagination") or {}
            events += (
                {
                    "type": "codex.resume_picker_visible",
                    "selected_index": pagination.get("selected_index"),
                    "total_count": pagination.get("total_count"),
                    "filter": (p["resume_surface"].get("filter") or {}).get("selected"),
                    "sort": (p["resume_surface"].get("sort") or {}).get("selected"),
                },
            )
        if p["update_surface"]["present"]:
            events += (
                {
                    "type": "codex.update_available",
                    "current_version": p["update_surface"]["current_version"],
                    "available_version": p["update_surface"]["available_version"],
                },
            )
        return ObservationDelta(
            updates=updates,
            evidence_refs=(ref,),
            semantic_events=events,
            diagnostics=item.diagnostics.messages,
        )

    def lower(  # noqa: PLR0912 - one branch per semantic action is intentional
        self, action: SemanticAction, snapshot: ObservationSnapshot
    ) -> Sequence[TerminalEffect]:
        prefix = action.action_id
        if isinstance(action, InsertPromptPayload):
            effects: list[TerminalEffect] = []
            for index, chunk in enumerate(action.chunks):
                if chunk.provenance is InputProvenance.USER_TYPED:
                    effects.append(
                        SendLiteralKeys(f"{prefix}:type:{index}", chunk.text, FAST_HUMANIZED_TYPING)
                    )
                else:
                    effects.extend(
                        (
                            PasteBuffer(f"{prefix}:paste:{index}", chunk.text),
                            SendNamedKey(f"{prefix}:tab:{index}", "Tab"),
                        )
                    )
            return tuple(effects)
        if isinstance(action, ClearComposer):
            return (SendNamedKey(f"{prefix}:clear", "C-u"),)
        if isinstance(action, CommitPromptSubmission):
            return (SendNamedKey(f"{prefix}:commit", "Enter"),)
        if isinstance(action, RequestUsage):
            # Codex command completion requires one Enter to accept /status
            # and one to open the status surface.  The controller has already
            # established a safe source surface; this is physical lowering only.
            return (
                SendNamedKey(f"{prefix}:dismiss", "Escape"),
                SendLiteralKeys(f"{prefix}:status", "/status"),
                SendNamedKey(f"{prefix}:command-enter", "Enter"),
                SendNamedKey(f"{prefix}:open-status", "Enter"),
            )
        if isinstance(action, (DismissOverlay, SendInterrupt)):
            return (SendNamedKey(f"{prefix}:escape", "Escape"),)
        if isinstance(action, AnswerQuestion):
            if action.mode is QuestionAnswerMode.DECLINE:
                return (SendNamedKey(f"{prefix}:decline", "Escape"),)
            if action.mode is QuestionAnswerMode.MULTIPLE:
                raise ValueError("Codex fixture corpus has no multi-select question surface")
            choice_id = action.selections[0].stable_choice_id if action.selections else None
            return _lower_menu_answer(prefix, choice_id, action.custom_answer)
        if isinstance(action, AnswerPermission):
            return _lower_menu_answer(prefix, action.response_id, None)
        if isinstance(action, SelectModel):
            return _lower_model_selection(action, snapshot)
        if isinstance(action, OpenModelPicker):
            return _open_model_picker(action, snapshot)
        raise ValueError(f"Codex lowering does not support {type(action).__name__}")


def _composer(clean: str) -> dict[str, Any]:
    lines = clean.splitlines()
    matches = [(i, _PROMPT.match(line)) for i, line in enumerate(lines)]
    matches = [(i, m) for i, m in matches if m and not _MENU.match(lines[i])]
    if not matches:
        return {
            "visible": False,
            "text": None,
            "normalized_text": None,
            "fingerprint": None,
            "partial": None,
            "placeholder": None,
            "visible_lines": [],
        }
    i, match = matches[-1]
    visible_lines = [lines[i]]
    content_lines = [match.group("text")]
    for line in lines[i + 1 :]:
        stripped = line.strip()
        if (
            not stripped
            or _PROMPT.match(line)
            or line.lstrip().startswith("•")
            or _FOOTER.match(line)
        ):
            break
        visible_lines.append(line)
        content_lines.append(stripped)
    text = "\n".join(content_lines)
    placeholder = (
        text
        if text.lower().startswith(("find and fix", "explain this codebase", "use /skills"))
        else None
    )
    value = "" if placeholder else text
    normalized = re.sub(r"\s+", " ", value).strip()
    return {
        "visible": True,
        "text": value,
        "normalized_text": normalized,
        "fingerprint": hashlib.sha256(normalized.encode()).hexdigest(),
        "partial": False,
        "placeholder": placeholder,
        "visible_lines": visible_lines,
    }


def _structured_surfaces(clean: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Recognize complete, live Codex decision surfaces and fail closed.

    Numbered prose is common in assistant output and scrollback.  It becomes an
    actionable surface only when it is part of a local modal: a semantic title
    immediately adjacent to a contiguous menu and either enclosing box geometry
    or Codex's Enter/Escape control chrome.  In particular, title lookup never
    walks back through arbitrary transcript history.

    Model, reasoning, update, and resume menus are retained as their own
    evidence, not misclassified as user questions or permissions.
    """
    lines = clean.splitlines()
    candidate = _live_numbered_surface(lines)
    if candidate is None:
        return (
            {"present": False, "reason": "no recognized live Codex question surface"},
            {"present": False, "reason": "no recognized live Codex permission surface"},
        )
    title, rows = candidate
    lowered = title.lower()
    if any(word in lowered for word in ("model", "reasoning", "update", "resume")):
        return (
            {"present": False, "reason": "numbered menu is a non-decision Codex surface"},
            {"present": False, "reason": "numbered menu is a non-decision Codex surface"},
        )
    if any(word in lowered for word in ("permission", "approve", "allow", "deny")):
        return (
            {"present": False, "reason": "permission surface active"},
            {
                "present": True,
                "title": title,
                "choices": rows,
                "selected": next((r["id"] for r in rows if r["selected"]), None),
            },
        )
    if any(word in lowered for word in ("question", "choose", "select")):
        return (
            {
                "present": True,
                "prompt": title,
                "choices": rows,
                "selection_mode": "single",
                "custom_answer": False,
            },
            {"present": False, "reason": "question surface active"},
        )
    return (
        {"present": False, "reason": "numbered menu has no question semantics"},
        {"present": False, "reason": "numbered menu has no permission semantics"},
    )


def _live_numbered_surface(lines: list[str]) -> tuple[str, list[dict[str, Any]]] | None:
    """Return one bounded, complete live menu, never a transcript-wide guess."""

    for start in range(len(lines)):
        first = _numbered_row(lines[start])
        if first is None:
            continue
        title_index = _adjacent_surface_title(lines, start)
        if title_index is None:
            continue
        rows = [first]
        end = start + 1
        while end < len(lines):
            row = _numbered_row(lines[end])
            if row is None:
                break
            rows.append(row)
            end += 1
        # A single numbered line is as likely to be prose or an annotation as a
        # choice menu.  Codex's current actionable surfaces provide a set.
        if len(rows) < _MINIMUM_MENU_CHOICES or not _has_live_menu_chrome(
            lines, title_index, end
        ):
            continue
        return (_surface_text(lines[title_index]), rows)
    return None


def _adjacent_surface_title(lines: list[str], menu_start: int) -> int | None:
    """Find a title directly above a menu, allowing only one blank spacer."""

    index = menu_start - 1
    blank_lines = 0
    while index >= 0 and not _surface_text(lines[index]):
        blank_lines += 1
        if blank_lines > 1:
            return None
        index -= 1
    if index < 0:
        return None
    title = _surface_text(lines[index])
    if not _is_semantic_decision_title(title):
        return None
    return index


def _has_live_menu_chrome(lines: list[str], title_index: int, menu_end: int) -> bool:
    """Require local modal structure, not merely decision-looking words."""

    before = lines[title_index - 1] if title_index else ""
    after = lines[menu_end] if menu_end < len(lines) else ""
    after_next = lines[menu_end + 1] if menu_end + 1 < len(lines) else ""
    enclosed_box = bool(
        _BOX_TOP.match(before)
        and (_BOX_BOTTOM.match(after) or _BOX_BOTTOM.match(after_next))
    )
    controls = _CODEX_MENU_CONTROLS.match(_surface_text(after)) or _CODEX_MENU_CONTROLS.match(
        _surface_text(after_next)
    )
    return enclosed_box or bool(controls)


def _surface_text(line: str) -> str:
    """Remove a modal gutter while retaining all text as raw frame evidence."""

    match = _BOX_GUTTER.match(line)
    text = match.group("content") if match else line
    return re.sub(r"\s+", " ", text).strip()


def _numbered_row(line: str) -> dict[str, Any] | None:
    match = _NUMBERED.match(_surface_text(line))
    if match is None:
        return None
    label = re.sub(r"\s+", " ", match.group("label")).strip()
    return {
        "id": match.group("number"),
        "label": label,
        "number": int(match.group("number")),
        "highlighted": bool(match.group("pointer")),
        "selected": None,
        "checked": None,
        "disabled": "disabled" in label.lower(),
    }


def _is_semantic_decision_title(title: str) -> bool:
    lowered = title.lower()
    return any(
        word in lowered
        for word in ("question", "choose", "select", "permission", "approve", "allow", "deny")
    )


def _question_observed(
    surface: dict[str, Any],
    ref: object,
    now: datetime,
    revision: ObservationRevision,
    *,
    absent_confirmed: bool,
) -> Observed[object]:
    if not surface["present"]:
        return Observed.without_value(
            Knowledge.ABSENT if absent_confirmed else Knowledge.UNKNOWN,
            evidence=(ref,),
            observed_at=now,
            revision=revision,
            explanation=surface["reason"],
        )  # type: ignore[arg-type]
    choices = tuple(
        ChoiceState(
            row["id"],
            row["label"],
            number=row["number"],
            selected=row["selected"],
            highlighted=row["highlighted"],
            checked=row["checked"],
            disabled=row["disabled"],
        )
        for row in surface["choices"]
    )
    return Observed.present(
        QuestionState(
            hashlib.sha256(surface["prompt"].encode()).hexdigest()[:16],
            surface["prompt"],
            choices,
            surface["selection_mode"],
            None,
            (),
            surface["custom_answer"],
            None,
            "Enter",
            "Escape",
            (),
        ),
        evidence=(ref,),
        observed_at=now,
        revision=revision,
    )  # type: ignore[arg-type]


def _permission_observed(
    surface: dict[str, Any],
    ref: object,
    now: datetime,
    revision: ObservationRevision,
    *,
    absent_confirmed: bool,
) -> Observed[object]:
    if not surface["present"]:
        return Observed.without_value(
            Knowledge.ABSENT if absent_confirmed else Knowledge.UNKNOWN,
            evidence=(ref,),
            observed_at=now,
            revision=revision,
            explanation=surface["reason"],
        )  # type: ignore[arg-type]
    choices = tuple(
        ChoiceState(
            row["id"],
            row["label"],
            number=row["number"],
            selected=row["selected"],
            highlighted=row["highlighted"],
            checked=row["checked"],
            disabled=row["disabled"],
        )
        for row in surface["choices"]
    )
    return Observed.present(
        PermissionRequestState(
            hashlib.sha256(surface["title"].encode()).hexdigest()[:16],
            None,
            None,
            surface["title"],
            choices,
            surface["selected"],
            frozenset(),
        ),
        evidence=(ref,),
        observed_at=now,
        revision=revision,
    )  # type: ignore[arg-type]


def _lower_menu_answer(
    prefix: str, choice_id: str | None, custom_answer: str | None
) -> Sequence[TerminalEffect]:
    if custom_answer is not None:
        return (
            SendLiteralKeys(f"{prefix}:custom", custom_answer, FAST_HUMANIZED_TYPING),
            SendNamedKey(f"{prefix}:confirm", "Enter"),
        )
    if choice_id is None or not choice_id.isdigit():
        raise ValueError("Codex menu lowering requires fixture-backed numeric choice identity")
    return (SendLiteralKeys(f"{prefix}:choice", choice_id, FAST_HUMANIZED_TYPING),)


def _model_readbacks(clean: str) -> list[dict[str, str | None]]:
    rows: list[dict[str, str | None]] = []
    for match in _MODEL.finditer(clean):
        rows.append(
            {
                "model_id": match.group("model"),
                "effort": normalize_effort(match.group("effort")),
                "display_name": match.group("model"),
                "source": "header",
            }
        )
    for match in _FOOTER.finditer(clean):
        rows.append(
            {
                "model_id": match.group("model"),
                "effort": normalize_effort(match.group("effort")),
                "display_name": match.group("model"),
                "source": "footer",
            }
        )
    return rows


def _codex_row_is_highlighted(clean: str, number: int | None) -> bool:
    """Keep cursor/highlight distinct from Codex's ``(current)`` marker."""

    if number is None:
        return False
    return bool(re.search(rf"^\s*[›>]\s*{number}\.\s+", clean, re.MULTILINE))


def _codex_model_configuration(
    choices: list[dict[str, Any]],
    effort_choices: list[dict[str, Any]],
    readbacks: list[dict[str, str | None]],
    clean: str,
) -> dict[str, Any]:
    """Represent visible configuration without claiming it is runtime-active.

    The corpus currently includes Codex's model stage but no effort-editor
    frame. Header/footer effort remains useful evidence about the currently
    configured row; a newly selected row still needs independent active-model
    readback before the controller can report success.
    """

    reasoning_title = re.search(r"Select Reasoning Level for\s+(?P<model>\S+)", clean, re.I)
    stage = "model" if choices else "effort" if effort_choices and reasoning_title else "none"
    configured = next((row["model_id"] for row in choices if row["current"]), None)
    if reasoning_title is not None:
        configured = reasoning_title.group("model")
    highlighted = next((row["model_id"] for row in choices if row["highlighted"]), None)
    parameters: list[tuple[str, str | bool | None]] = []
    if readbacks and readbacks[-1].get("effort") is not None:
        parameters.append(("effort", readbacks[-1]["effort"]))
    if stage == "effort":
        highlighted_effort = next(
            (
                row["effort"]
                for row in effort_choices
                if _codex_row_is_highlighted(clean, row["number"])
            ),
            None,
        )
        parameters = [("stage", "effort")]
        if highlighted_effort is not None:
            parameters.append(("effort", highlighted_effort))
        parameters.extend(
            (f"effort_option.{row['effort']}", str(row["number"]))
            for row in effort_choices
        )
    return {
        "picker_visible": bool(choices or effort_choices),
        "stage": stage,
        "available": choices,
        "highlighted_model_id": highlighted,
        "selected_model_id": configured,
        "configured_model_id": configured,
        "pending_changes": False if stage == "model" else True if stage == "effort" else None,
        "parameters": parameters,
        # Retain this future-stage material in evidence, but do not pretend
        # model rows are effort rows when the editor itself is not visible.
        "effort_choices": effort_choices,
    }


def _lower_model_selection(
    action: SelectModel, snapshot: ObservationSnapshot
) -> Sequence[TerminalEffect]:
    """Lower only from a current Codex picker; no stale row-position guesses."""

    config = snapshot.model_configuration
    if config.knowledge is not Knowledge.PRESENT or config.value is None:
        raise ValueError("Codex model selection requires current picker configuration evidence")
    if snapshot.surface.knowledge is not Knowledge.PRESENT or snapshot.surface.value is None:
        raise ValueError("Codex model selection requires a known current surface")
    if snapshot.surface.value.primary is not SurfaceKind.MODEL_PICKER:
        raise ValueError("Codex model selection will not reopen an unobserved picker")
    parameters = dict(config.value.parameters)
    if parameters.get("stage") == "effort":
        if action.effort is None or parameters.get("effort") == action.effort:
            return (SendNamedKey(f"{action.action_id}:confirm-configuration", "Enter"),)
        option = parameters.get(f"effort_option.{action.effort}")
        if not isinstance(option, str):
            raise ValueError("requested Codex effort is absent from the observed parameter picker")
        return (
            SendLiteralKeys(
                f"{action.action_id}:select-effort", option, FAST_HUMANIZED_TYPING
            ),
        )
    candidates = [
        choice for choice in config.value.available if choice.stable_choice_id == action.model_id
    ]
    if len(candidates) != 1:
        raise ValueError("Codex target model is absent or ambiguous in the current picker")
    target = candidates[0]
    if target.disabled is True or target.number is None:
        raise ValueError("Codex target model is disabled or lacks numeric picker identity")
    return (
        SendLiteralKeys(
            f"{action.action_id}:select-model", str(target.number), FAST_HUMANIZED_TYPING
        ),
    )


def _open_model_picker(
    action: OpenModelPicker, snapshot: ObservationSnapshot
) -> Sequence[TerminalEffect]:
    if snapshot.surface.knowledge is not Knowledge.PRESENT or snapshot.surface.value is None:
        raise ValueError("Codex model picker requires a known safe surface")
    if snapshot.surface.value.primary not in {SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}:
        raise ValueError("Codex model picker will not replace an unobserved overlay")
    return (
        SendLiteralKeys(f"{action.action_id}:open-model", "/model", FAST_HUMANIZED_TYPING),
        SendNamedKey(f"{action.action_id}:open-model-enter", "Enter"),
    )


def _busy(clean: str) -> dict[str, Any] | None:
    match = _BUSY.search(clean)
    if not match:
        return None
    return {
        "label": match.group("label").strip(),
        "seconds": int(match.group("seconds")),
        "minutes": int((match.group("elapsed") or "0m").strip().rstrip("m")),
    }


def _generation(busy: dict[str, Any] | None) -> GenerationState:
    if busy is None:
        return GenerationState(GenerationPhase.IDLE, False, False, None, None, None)
    label = busy["label"].lower()
    phase = (
        GenerationPhase.STARTING
        if "starting" in label
        else GenerationPhase.RUNNING_TOOL
        if "tool" in label
        else GenerationPhase.THINKING
    )
    return GenerationState(
        phase, True, True, timedelta(seconds=busy["seconds"] + busy["minutes"] * 60), None, None
    )


def _tail(doc: dict[str, Any], busy: dict[str, Any] | None) -> TranscriptTailState:
    segments = doc.get("segments") if isinstance(doc.get("segments"), list) else []
    users = [s for s in segments if isinstance(s, dict) and s.get("type") == "user"]
    assistants = [s for s in segments if isinstance(s, dict) and s.get("type") == "assistant"]

    def ref(segment: dict[str, Any], role: str) -> TurnRef:
        stable_id = hashlib.sha256(f"{role}:{segment.get('text', '')}".encode()).hexdigest()[:16]
        return TurnRef(stable_id, role)

    fingerprints = tuple(
        hashlib.sha256(str(s.get("text", "")).encode()).hexdigest() for s in users[-8:]
    )
    latest = str((assistants or users or [{}])[-1].get("text", ""))
    return TranscriptTailState(
        ref(users[-1], "user") if users else None,
        ref(assistants[-1], "assistant") if assistants else None,
        fingerprints,
        bool(busy and assistants),
        bool(assistants and not busy),
        hashlib.sha256(latest.encode()).hexdigest() if latest else None,
        len(segments),
    )


def _tools(doc: dict[str, Any]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for segment in doc.get("segments", []):
        if not isinstance(segment, dict) or segment.get("type") != "tool_call":
            continue
        raw = dict(segment)
        title = segment.get("title") if isinstance(segment.get("title"), str) else None
        result = segment.get("result") if isinstance(segment.get("result"), str) else None
        running = segment.get("running") is True
        match = _NAMESPACED_TOOL.match(title or "")
        tools.append(
            {
                "tool_name": match.group("name") if match else None,
                "command": title,
                "input": segment.get("input"),
                "output": result,
                "status": "running" if running else "complete" if result is not None else "unknown",
                "elided": segment.get("elided") is True,
                "running": running,
                "paths_read": [],
                "paths_written": [],
                "raw": raw,
            }
        )
    return tools


def _tool_activity(tools: list[dict[str, Any]]) -> ToolActivityState:
    interactions = tuple(
        ToolInteraction(
            s.get("tool_name"),
            s.get("command"),
            tuple(s.get("paths_read", ())),
            tuple(s.get("paths_written", ())),
            s.get("status"),
            None,
            None,
        )
        for s in tools[-8:]
    )
    return ToolActivityState(
        tuple(item for item in interactions if item.status == "running"),
        tuple(item for item in interactions if item.status != "running"),
    )


def _mcp_startup(clean: str) -> dict[str, Any] | None:
    match = _MCP_STARTUP.search(clean)
    if match is None:
        return None
    return {
        "raw_line": match.group("raw").strip(),
        "started_count": int(match.group("started")),
        "total_count": int(match.group("total")),
        "server_names": [
            name.strip() for name in match.group("servers").split(",") if name.strip()
        ],
        "elapsed_seconds": int(match.group("seconds")),
        "interruptible": True,
    }


def _background_count(clean: str) -> int | None:
    match = _BACKGROUND.search(clean)
    return int(match.group("count")) if match else None


def _resume_surface(clean: str) -> dict[str, Any]:
    lines = clean.splitlines()
    title_index = next(
        (index for index, line in enumerate(lines) if line.strip() == _RESUME_TITLE), None
    )
    if title_index is None:
        return {
            "present": False,
            "reason": "Codex resume picker title is not visible",
            "title": None,
            "search_text": None,
            "filter": None,
            "sort": None,
            "pagination": None,
            "sessions": [],
            "controls": {},
            "raw_lines": [],
        }

    options_index, options = next(
        (
            (index, match)
            for index, line in enumerate(lines[title_index + 1 :], title_index + 1)
            if (match := _RESUME_OPTIONS.search(line)) is not None
        ),
        (None, None),
    )
    page_index, page = next(
        (
            (index, match)
            for index, line in enumerate(lines[title_index + 1 :], title_index + 1)
            if (match := _RESUME_PAGE.search(line)) is not None
        ),
        (None, None),
    )
    sessions: list[dict[str, Any]] = []
    for index, line in enumerate(lines[title_index + 1 :], title_index + 1):
        match = _RESUME_SESSION.match(line)
        if match is None:
            continue
        sessions.append(
            {
                "age": re.sub(r"\s+", "", match.group("age")[:-3]) + " ago",
                "preview": match.group("preview").strip(),
                "highlighted": match.group("highlighted") is not None,
                "raw_line": line,
                "line_number": index + 1,
            }
        )

    search_text = None
    filter_state = None
    sort_state = None
    if options is not None and options_index is not None:
        before_filter = lines[options_index][: options.start()].strip()
        search_text = "" if before_filter == "Type to search" else before_filter
        filter_state = {
            "selected": options.group("filter").strip(),
            "available": [
                options.group("filter").strip(),
                options.group("filter_other").strip(),
            ],
        }
        sort_state = {
            "selected": options.group("sort").strip(),
            "available": [options.group("sort").strip(), options.group("sort_other").strip()],
        }
    pagination = (
        {
            "selected_index": int(page.group("selected")),
            "total_count": int(page.group("total")),
            "percent": int(page.group("percent")),
        }
        if page is not None
        else None
    )
    raw_end = min(len(lines), (page_index + 3) if page_index is not None else len(lines))
    return {
        "present": True,
        "reason": None,
        "title": _RESUME_TITLE,
        "search_text": search_text,
        "filter": filter_state,
        "sort": sort_state,
        "pagination": pagination,
        "sessions": sessions,
        "controls": {"resume": "enter", "exit": ["esc", "ctrl+c"]},
        "raw_lines": lines[title_index:raw_end],
    }


def _update_surface(clean: str) -> dict[str, Any]:
    lines = clean.splitlines()
    match = _UPDATE_TITLE.search(clean)
    if match is None:
        return {
            "present": False,
            "current_version": None,
            "available_version": None,
            "release_url": None,
            "choices": [],
            "controls": {},
            "raw_lines": [],
        }
    title_index = max(
        index for index, line in enumerate(lines) if _UPDATE_TITLE.search(line) is not None
    )
    rows = []
    for index, line in enumerate(lines[title_index + 1 :], title_index + 1):
        row = _NUMBERED.match(line)
        if row is None:
            continue
        rows.append(
            {
                "id": row.group("number"),
                "label": row.group("label").strip(),
                "highlighted": row.group("pointer") is not None,
                "disabled": "disabled" in row.group("label").lower(),
                "raw_line": line,
                "line_number": index + 1,
            }
        )
    release_url = next(
        (
            url.group(0)
            for line in lines[title_index + 1 :]
            if (url := re.search(r"https?://\S+", line)) is not None
        ),
        None,
    )
    later_composer = any(
        index > title_index and _PROMPT.match(line) and not _MENU.match(line)
        for index, line in enumerate(lines)
    )
    return {
        "present": not later_composer,
        "historical": later_composer,
        "current_version": match.group("current"),
        "available_version": match.group("available"),
        "release_url": release_url,
        "choices": rows,
        "controls": {"confirm": "enter"},
        "raw_lines": lines[title_index:],
    }


def _current_shell_error(clean: str) -> bool:
    matches = list(_INVALID_RESUME.finditer(clean))
    if not matches:
        return False
    last_error = matches[-1].start()
    offset = 0
    for line in clean.splitlines(keepends=True):
        if offset > last_error and _PROMPT.match(line.rstrip("\r\n")) and not _MENU.match(line):
            return False
        offset += len(line)
    return True


def _status_evidence(  # noqa: PLR0912, PLR0915 - explicit status field inventory
    clean: str, parsed_usage: Any
) -> dict[str, Any]:
    """Retain the latest Codex status panel without widening shared state.

    Scrollback can contain many old status panels.  Every retained field is
    therefore taken from the latest visible heading and carries the exact
    source line and its one-based frame line number.
    """

    lines = clean.splitlines()
    last_composer = max(
        (
            index
            for index, line in enumerate(lines)
            if _PROMPT.match(line) is not None and _MENU.match(line) is None
        ),
        default=-1,
    )
    headings = []
    for index, line in enumerate(lines):
        match = _STATUS_HEADING.search(line)
        if match is None:
            continue
        close = next(
            (
                candidate
                for candidate in range(index + 1, len(lines))
                if lines[candidate].lstrip().startswith("╰")
            ),
            len(lines),
        )
        structured_labels = {
            field.group("label").lower()
            for candidate in lines[index + 1 : close]
            if (field := _STATUS_FIELD.match(candidate.strip().strip("│").strip())) is not None
        }
        if structured_labels.intersection({"account", "session", "collaboration mode"}):
            headings.append((index, match))
    start = headings[-1][0] if headings else None
    end = len(lines)
    if start is not None:
        for index in range(start + 1, len(lines)):
            if lines[index].lstrip().startswith("╰"):
                end = index
                break
    panel_present = start is not None
    panel_active = start is not None and start > last_composer
    knowledge_when_missing = "absent" if panel_present else "unknown"

    def missing() -> dict[str, Any]:
        return {
            "knowledge": knowledge_when_missing,
            "value": None,
            "raw_line": None,
            "line_number": None,
        }

    names = (
        "cli_version",
        "directory",
        "workspace",
        "permissions",
        "agents_md",
        "account",
        "plan",
        "collaboration_mode",
        "session_id",
        "model_id",
        "reasoning_effort",
        "summary_mode",
    )
    fields = {name: missing() for name in names}

    def present(name: str, value: str, raw_line: str, line_number: int) -> None:
        fields[name] = {
            "knowledge": "present",
            "value": value,
            "raw_line": raw_line,
            "line_number": line_number,
        }

    raw_lines: list[str] = []
    if start is not None:
        heading = headings[-1][1]
        heading_raw = lines[start]
        present("cli_version", heading.group("version").strip(), heading_raw, start + 1)
        raw_lines.append(heading_raw)
        for index in range(start + 1, end):
            raw_line = lines[index]
            content = raw_line.strip().strip("│").strip()
            match = _STATUS_FIELD.match(content)
            if match is None:
                if "up-to-date" in content.lower() or "stale" in content.lower():
                    raw_lines.append(raw_line)
                continue
            raw_lines.append(raw_line)
            label = match.group("label").lower()
            value = match.group("value").strip()
            line_number = index + 1
            if label == "model":
                model_match = re.match(
                    r"(?P<model>[^\s(]+)(?:\s*\(reasoning\s+(?P<effort>[^,)]+),\s*"
                    r"summaries\s+(?P<summary>[^)]+)\))?",
                    value,
                    re.I,
                )
                if model_match:
                    present("model_id", model_match.group("model"), raw_line, line_number)
                    if model_match.group("effort"):
                        present(
                            "reasoning_effort",
                            normalize_effort(model_match.group("effort"))
                            or model_match.group("effort").strip(),
                            raw_line,
                            line_number,
                        )
                    if model_match.group("summary"):
                        present(
                            "summary_mode",
                            model_match.group("summary").strip(),
                            raw_line,
                            line_number,
                        )
            elif label == "account":
                account_match = re.match(r"(?P<account>.*?)(?:\s+\((?P<plan>[^()]*)\))?$", value)
                if account_match:
                    present(
                        "account", account_match.group("account").strip(), raw_line, line_number
                    )
                    if account_match.group("plan"):
                        present("plan", account_match.group("plan").strip(), raw_line, line_number)
            else:
                key = {
                    "directory": "directory",
                    "workspace": "workspace",
                    "permissions": "permissions",
                    "agents.md": "agents_md",
                    "collaboration mode": "collaboration_mode",
                    "session": "session_id",
                }[label]
                present(key, value, raw_line, line_number)

        # Quota rows are structured by the usage parser, but retaining their
        # exact latest-panel render makes later parser revisions auditable.
        for index in range(start + 1, end):
            if "limit:" in lines[index].lower():
                raw_lines.append(lines[index])

    # Quota extraction and freshness are selected by the shared bounded parser;
    # do not independently scan the entire frame and revive an old warning.
    advisory = next(
        (notice.text for notice in parsed_usage.notices if notice.kind == "stale_limits"), None
    )
    return {
        "present": panel_active,
        "historical": panel_present and not panel_active,
        "source": parsed_usage.source,
        "fetched_at": parsed_usage.fetched_at,
        "freshness": parsed_usage.freshness.value if panel_present else "unknown",
        "freshness_advisory": advisory,
        "fields": fields,
        "raw": parsed_usage.raw,
        "raw_lines": raw_lines,
    }


def _stale_advisory(clean: str) -> str | None:
    for line in clean.splitlines():
        lowered = line.lower()
        # The ordinary settings link says "for up-to-date information" and
        # is not a warning.  Only Codex's explicit stale advisory changes
        # collection freshness.
        if re.search(r"\blimits?\s+may\s+be\s+(?:stale|out\s+of\s+date)\b", lowered):
            return line.strip()
    return None


def _capture_sequence(item: EvidenceEnvelope) -> int:
    # Frame ids remain opaque, so the observer's persisted frame sequence is the
    # authoritative monotonic input; this adapter uses a stable fallback only.
    raw = item.payload["raw_frame"]
    return int(raw.get("capture_sequence", 0))


def _dt(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


__all__ = ["CodexHarnessAdapter"]
