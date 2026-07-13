"""Claude Code evidence parsing, narrow observation projection, and lowering."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import asdict
from datetime import datetime
from uuid import uuid4

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
from murder.llm.harnesses.choice_prompt import parse_claude_code_choice_prompt
from murder.llm.harnesses.parsing import (
    _claude_code_slash_id,
    normalize_effort,
    parse_claude_code_model_choices,
    strip_ansi,
)
from murder.llm.harnesses.transcripts import parse_frames
from murder.llm.harnesses.usage import parse_claude_usage_pane

_RULE = re.compile(r"^[─-]{10,}$")
_PROMPT = re.compile(r"^\s*❯[\s\xa0]*(.*)$")
_MODEL = re.compile(
    r"\b(Opus|Sonnet|Haiku|Fable)\b(?:\s+\d+(?:\.\d+)*)?.*?\bwith\s+(low|medium|high|x\s*high|xhigh|max)\s+effort",
    re.I,
)
_BANNER_MODEL = re.compile(r"\b(Opus|Sonnet|Haiku|Fable)\s+\d+(?:\.\d+)*\s+·", re.I)
_EFFORT = re.compile(
    r"[●•○◐◈]\s*(low|medium|high|x\s*high|xhigh|max)(?:\s+effort)?"
    r"(?:\s+\(default\))?(?=\s*(?:·|←|→|$))",
    re.I,
)
_MODEL_PICKER_ROW = re.compile(r"^\s*(?P<pointer>[❯>])?\s*(?P<number>\d+)\.\s*(?P<body>.+?)\s*$")
_MODEL_PICKER_TITLE = re.compile(r"^\s*Select model\s*$", re.I | re.M)
_PERMISSION = re.compile(r"\b(?:permission|allow\s+(?:once|for\s+session)|deny|approve)\b", re.I)
_COMMAND = re.compile(r"(?:command|run)\s*:\s*`?([^`\n]+)`?", re.I)
_SPINNER = re.compile(r"(?:esc to interrupt|[✻✶✳✽✢⠁-⣿].*…)", re.I)
_AGENT = re.compile(r'(?:Agent\(([^)]+)\)|Agent\s+"([^"]+)"\s+completed)')
_AGENT_MANAGER_ROW = re.compile(r"^\s*(?P<marker>[●◯])\s+(?P<name>\S+)(?P<body>.*)$")
_AGENT_MANAGER_USAGE = re.compile(
    r"(?P<elapsed>\d+(?:\.\d+)?[hms])\s*·\s*↓\s*"
    r"(?P<tokens>\d+(?:\.\d+)?[kKmM]?)\s+tokens\s*$"
)
_TAB = re.compile(r"[☐☒✔]\s*([^←→☐☒✔]+?)(?=\s+[☐☒✔]|\s*→|$)")
_RESUME_TITLE = re.compile(
    r"^\s*Resume session(?:\s+\((?P<current>\d+)\s+of\s+(?P<total>\d+)\))?\s*$",
    re.I,
)
_RESUME_SEARCH = re.compile(r"⌕\s*(?P<text>.*?)\s*(?:│)?\s*$")
_RESUME_METADATA = re.compile(
    r"^\s*(?P<age>.+?)\s+·\s+(?P<branch>.+?)\s+·\s+"
    r"(?P<size>\d+(?:\.\d+)?(?:B|KB|MB|GB))\s+·\s+(?P<project_path>[/~].+?)\s*$",
    re.I,
)
_CONTEXT_CHARACTERISTIC = re.compile(
    r"^\s*(?P<percent>\d+(?:\.\d+)?)%\s+of your usage came from\s+"
    r"(?P<name>.+?)\s*$",
    re.I,
)
_CONTEXT_SECTION = re.compile(r"^\s*(?P<label>.+?)\s+% of usage\s*$", re.I)
_CONTEXT_CONTRIBUTOR = re.compile(
    r"^\s*(?P<name>\S(?:.*?\S)?)\s{2,}(?P<percent>\d+(?:\.\d+)?)%\s*$"
)
_CONTEXT_CATEGORIES = {
    "subagents": "subagent",
    "mcp servers": "mcp_server",
    "plugins": "plugin",
    "built-ins": "built_in",
    "built in": "built_in",
    "built-in tools": "built_in",
}
_ANSWERED_QUESTION = re.compile(
    r"(?:User declined to answer questions|(?:User )?answered(?: the)? question[s]?\s*:\s*.+)",
    re.I,
)


def _ref(envelope: EvidenceEnvelope) -> EvidenceRef:
    return envelope.ref()


def _present(
    value: object, ref: EvidenceRef, at: datetime, rev: ObservationRevision
) -> Observed[object]:
    return Observed.present(value, evidence=(ref,), observed_at=at, revision=rev)


def _without(
    knowledge: Knowledge, ref: EvidenceRef, at: datetime, rev: ObservationRevision
) -> Observed[object]:
    return Observed.without_value(knowledge, evidence=(ref,), observed_at=at, revision=rev)


def _composer(lines: list[str]) -> dict[str, object] | None:
    for index, line in enumerate(lines):
        match = _PROMPT.match(line)
        if match is None:
            continue
        before, after = index - 1, index + 1
        while before >= 0 and not lines[before].strip():
            before -= 1
        while after < len(lines) and not lines[after].strip():
            after += 1
        if (
            before < 0
            or after >= len(lines)
            or not (_RULE.match(lines[before].strip()) and _RULE.match(lines[after].strip()))
        ):
            continue
        raw = match.group(1).strip()
        placeholder = bool(re.match(r"(?:Try |Use |Ask |Plan,)", raw, re.I))
        return {
            "text": "" if placeholder else raw,
            "raw_text": raw,
            "placeholder": raw if placeholder else None,
            "cursor_visible": True,
            "focused": True,
            "accepts_submission": True,
            "partial": False,
        }
    return None


def _question(clean: str) -> dict[str, object] | None:
    parsed = parse_claude_code_choice_prompt(clean)
    if parsed is None:
        return None
    tabs = [
        match.group(1).strip()
        for line in clean.splitlines()
        if "←" in line and "→" in line
        for match in _TAB.finditer(line)
    ]
    choices = [
        {
            "stable_choice_id": f"number:{option.number}",
            "number": option.number,
            "label": option.label,
            "description": option.description or None,
            "checked": option.checked,
            "selected": option.number == parsed.selected_option.number
            if not parsed.submit_selected
            else False,
            "highlighted": option.number == parsed.selected_option.number
            if not parsed.submit_selected
            else False,
            "disabled": None,
            "current": None,
            "shortcut": str(option.number),
        }
        for option in parsed.options
    ]
    return {
        "question_id_hint": hashlib.sha256(parsed.question.encode()).hexdigest()[:16],
        "prompt_text": parsed.question,
        "choices": choices,
        "selection_mode": "multi" if parsed.multi_select else "single",
        "active_tab": tabs[0] if tabs else None,
        "visible_tabs": tabs,
        "allow_custom_answer": any(
            "type something" in str(item["label"]).lower() for item in choices
        ),
        "custom_answer_text": None,
        "submit_label": "Submit" if parsed.multi_select else parsed.footer,
        "decline_label": "Chat about this" if "Chat about this" in clean else None,
        "answered_summary": re.findall(
            r"(?:User declined to answer questions|Answered:\s*.+)", clean, re.I
        ),
        "submit_selected": parsed.submit_selected,
    }


def _answered_question_summaries(clean: str) -> list[str]:
    """Retain completed question outcomes after the picker has disappeared.

    An answered surface is historical evidence, not proof that a currently
    visible picker remains resolved.  Keeping it outside ``_question`` avoids
    silently dropping the result merely because the live menu is gone.
    """

    return [match.group(0).strip() for match in _ANSWERED_QUESTION.finditer(clean)]


def _trust_dialog(lines: list[str], question: dict[str, object] | None) -> dict[str, object] | None:
    """Parse Claude's startup trust dialog as its own renderer-specific surface.

    It deliberately is not promoted to ``PermissionRequestState``: trusting a
    workspace has materially different semantics from approving a tool call.
    The choices remain durable evidence so a future trust capability need not
    reconstruct them from raw frames.
    """

    start = next(
        (
            index
            for index, line in enumerate(lines)
            if "quick safety check:" in line.lower() or "accessing workspace:" in line.lower()
        ),
        None,
    )
    if start is None:
        return None
    workspace = next(
        (
            line.strip()
            for line in lines[start : min(start + 8, len(lines))]
            if line.strip().startswith(("/", "~/"))
        ),
        None,
    )
    choices = question.get("choices", []) if isinstance(question, dict) else []
    return {
        "workspace": workspace,
        "prompt_text": question.get("prompt_text") if isinstance(question, dict) else None,
        "choices": choices,
        "selected_choice": next(
            (
                choice.get("label")
                for choice in choices
                if isinstance(choice, dict) and choice.get("selected")
            ),
            None,
        ),
        "security_guide_visible": any("security guide" in line.lower() for line in lines[start:]),
        "confirm_control": next(
            (line.strip() for line in lines[start:] if "enter to confirm" in line.lower()),
            None,
        ),
    }


def _resume_picker(lines: list[str]) -> dict[str, object] | None:
    title_index, title_match = next(
        (
            (index, match)
            for index, line in enumerate(lines)
            if (match := _RESUME_TITLE.match(line)) is not None
        ),
        (None, None),
    )
    if title_index is None or title_match is None:
        return None

    sessions: list[dict[str, object]] = []
    for index in range(title_index + 1, len(lines)):
        metadata = _RESUME_METADATA.match(lines[index])
        if metadata is None:
            continue
        title_line = lines[index - 1].strip()
        marker = title_line[:1] if title_line[:1] in {"❯", "↓"} else None
        title = title_line[1:].strip() if marker else title_line
        if not title:
            continue
        sessions.append(
            {
                "ordinal": len(sessions),
                "title": title,
                "age": metadata.group("age"),
                "branch": metadata.group("branch"),
                "size": metadata.group("size"),
                "project_path": metadata.group("project_path"),
                "highlighted": marker == "❯",
                "scroll_marker": "down" if marker == "↓" else None,
                "disabled": None,
                "current": None,
                "default": None,
                # The picker fixture does not expose stable session IDs.
                "session_id": None,
            }
        )

    search_text = None
    for line in lines[title_index + 1 :]:
        search = _RESUME_SEARCH.search(line.rstrip())
        if search is not None:
            search_text = search.group("text").strip()
            break
    placeholder = "Search…" if search_text == "Search…" else None
    footer = next(
        (line.strip() for line in lines[title_index + 1 :] if "Type to search" in line), None
    )
    empty_message = next(
        (line.strip() for line in lines[title_index + 1 :] if "No sessions match" in line), None
    )
    return {
        "visible": True,
        "title": "Resume session",
        "pagination": {
            "current": int(title_match.group("current"))
            if title_match.group("current")
            else None,
            "total": int(title_match.group("total")) if title_match.group("total") else None,
        },
        "search": {
            "query": "" if placeholder else search_text,
            "placeholder": placeholder,
        },
        "sessions": sessions,
        "selected_ordinal": next(
            (row["ordinal"] for row in sessions if row["highlighted"]), None
        ),
        "empty": empty_message is not None,
        "empty_message": empty_message,
        "filters": {
            "current_repo_only": None,
            "current_branch_only": None,
        },
        "controls": {
            "toggle_repo_filter": "Ctrl+A" if footer and "Ctrl+A" in footer else None,
            "toggle_branch_filter": "Ctrl+B" if footer and "Ctrl+B" in footer else None,
            "preview": "Space" if footer and "Space to preview" in footer else None,
            "rename": "Ctrl+R" if footer and "Ctrl+R" in footer else None,
            "cancel": "Esc" if footer and "Esc to cancel" in footer else None,
        },
        "raw_footer": footer,
    }


def _model(clean: str) -> dict[str, object]:
    choices = parse_claude_code_model_choices(clean)
    active = list(_MODEL.finditer(clean))
    banner = list(_BANNER_MODEL.finditer(clean))
    model_id = (
        _claude_code_slash_id(active[-1].group(1))
        if active
        else (_claude_code_slash_id(banner[-1].group(1)) if banner else None)
    )
    # Header text is the independent active-runtime readback.  The effort
    # control in a visible picker is staged/default configuration and may
    # legitimately differ from the model currently running in this session.
    active_effort = normalize_effort(active[-1].group(2)) if active else None
    picker_effort_matches = list(_EFFORT.finditer(clean)) if choices else []
    picker_effort = (
        normalize_effort(picker_effort_matches[-1].group(1)) if picker_effort_matches else None
    )
    rows_by_number: dict[int, dict[str, bool]] = {}
    for line in clean.splitlines():
        row = _MODEL_PICKER_ROW.match(line)
        if row is None:
            continue
        number = int(row.group("number"))
        body = row.group("body")
        rows_by_number[number] = {
            "highlighted": row.group("pointer") is not None,
            # A checkmark establishes saved/default selection, not activation.
            "selected": "✔" in body or "✓" in body,
        }
    available: list[dict[str, object]] = []
    for choice in choices:
        visual = rows_by_number.get(choice.index or -1, {})
        selected = bool(visual.get("selected"))
        available.append(
            {
                "stable_choice_id": choice.model_id,
                "label": choice.label,
                "number": choice.index,
                "selected": selected,
                "current": selected,
                "highlighted": bool(visual.get("highlighted")),
                "checked": selected,
                "disabled": None,
                "shortcut": str(choice.index) if choice.index is not None else None,
            }
        )
    selected_model = next((row["stable_choice_id"] for row in available if row["selected"]), None)
    highlighted_model = next(
        (row["stable_choice_id"] for row in available if row["highlighted"]), None
    )
    return {
        "active": {
            "model_id": model_id,
            "effort": active_effort,
            "display_name": model_id,
            "provider": "anthropic" if model_id else None,
        },
        "configuration": {
            "available": available,
            "highlighted_model_id": highlighted_model,
            "selected_model_id": selected_model,
            "configured_model_id": selected_model,
            "pending_changes": (
                highlighted_model is not None and highlighted_model != selected_model
                if selected_model is not None
                else None
            ),
            "parameters": [("effort", picker_effort)] if picker_effort else [],
            # Retain renderer-specific stage and controls for later
            # reinterpretation without widening the shared observation model.
            "stage": "model_picker" if _MODEL_PICKER_TITLE.search(clean) else None,
            "activation_control": {
                "set_default_key": "Enter",
                "use_current_session_key": "s",
            }
            if choices
            else None,
        },
    }


def _context_composition(lines: list[str]) -> dict[str, object] | None:
    """Retain Claude's renderer-specific usage contributors without snapshot promotion."""
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if "what's contributing to your limits usage?" in line.lower()
        ),
        None,
    )
    if start is None:
        return None
    end = next(
        (
            index
            for index in range(start + 1, len(lines))
            if re.match(r"^\s*(?:d to day|Esc to cancel)\b", lines[index], re.I)
        ),
        len(lines),
    )
    raw_lines = [line.strip() for line in lines[start:end] if line.strip()]
    characteristics: list[dict[str, object]] = []
    contributors: list[dict[str, object]] = []
    category: str | None = None
    for line in lines[start:end]:
        if match := _CONTEXT_CHARACTERISTIC.match(line):
            characteristics.append(
                {
                    "name": match.group("name"),
                    "percent_of_usage": float(match.group("percent")),
                    "period": "last_24h",
                }
            )
            continue
        if match := _CONTEXT_SECTION.match(line):
            category = _CONTEXT_CATEGORIES.get(match.group("label").strip().lower())
            continue
        if category is not None and (match := _CONTEXT_CONTRIBUTOR.match(line)):
            contributors.append(
                {
                    "category": category,
                    "name": match.group("name"),
                    "percent_of_usage": float(match.group("percent")),
                }
            )
    return {
        "period": "last_24h" if any("Last 24h" in line for line in raw_lines) else None,
        "source": "local_sessions",
        "excludes_other_devices": any(
            "does not include other devices" in line.lower() for line in raw_lines
        ),
        "characteristics": characteristics,
        "contributors": contributors,
        "raw_lines": raw_lines,
    }


def _choice_states(rows: object) -> tuple[ChoiceState, ...]:
    if not isinstance(rows, list):
        return ()
    return tuple(
        ChoiceState(
            stable_choice_id=item.get("stable_choice_id"),
            label=str(item.get("label") or ""),
            description=item.get("description"),
            number=item.get("number"),
            shortcut=item.get("shortcut"),
            selected=item.get("selected"),
            highlighted=item.get("highlighted"),
            checked=item.get("checked"),
            disabled=item.get("disabled"),
            current=item.get("current"),
        )
        for item in rows
        if isinstance(item, dict)
    )


def _scaled_number(value: str) -> int:
    suffix = value[-1:].lower()
    multiplier = {"k": 1_000, "m": 1_000_000}.get(suffix, 1)
    number = value[:-1] if multiplier != 1 else value
    return int(float(number) * multiplier)


def _elapsed_seconds(value: str) -> int:
    suffix = value[-1].lower()
    return int(float(value[:-1]) * {"h": 3600, "m": 60, "s": 1}[suffix])


def _agent_manager(lines: list[str]) -> dict[str, object] | None:
    if not any("↑/↓ to select · Enter to view" in line for line in lines):
        return None
    rows: list[dict[str, object]] = []
    for line in lines:
        match = _AGENT_MANAGER_ROW.match(line)
        if match is None:
            continue
        name, body = match.group("name"), match.group("body").strip()
        relationship = "main" if name == "main" else "child"
        usage = _AGENT_MANAGER_USAGE.search(body) if relationship == "child" else None
        if relationship == "child" and usage is None:
            continue
        task = body[: usage.start()].strip() if usage else None
        rows.append(
            {
                "name": name,
                "selected": match.group("marker") == "●",
                "relationship": relationship,
                "status": "active" if relationship == "child" else None,
                "task": task or None,
                "elapsed_seconds": _elapsed_seconds(usage.group("elapsed")) if usage else None,
                "token_count": _scaled_number(usage.group("tokens")) if usage else None,
                "raw_line": line.strip(),
            }
        )
    main = next((row for row in rows if row["relationship"] == "main"), None)
    children = [row for row in rows if row["relationship"] == "child"]
    if main is None:
        return None
    return {
        "visible": True,
        "selected_agent": next((row["name"] for row in rows if row["selected"]), None),
        "main": {
            "name": main["name"],
            "selected": main["selected"],
            "relationship": "main",
        },
        "counts": {
            "active_children": sum(row["status"] == "active" for row in children),
            "recent_children": sum(row["status"] != "active" for row in children),
        },
        "rows": rows,
    }


def _prior_subagent_statuses(history: Sequence[EvidenceEnvelope]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for envelope in history:
        payload = envelope.payload
        if not isinstance(payload, dict):
            continue
        for agent in payload.get("subagents", []):
            if not isinstance(agent, dict) or not agent.get("status"):
                continue
            for identity in (agent.get("task"), agent.get("name")):
                if identity:
                    statuses[str(identity)] = str(agent["status"])
    return statuses


class ClaudeCodeAdapter:
    """Adapter edge only: parse broadly, project narrowly, lower purely."""

    parser_version = "claude-code-evidence/v2"

    def parse_evidence(
        self, frame: TerminalFrame, history: Sequence[EvidenceEnvelope]
    ) -> Sequence[EvidenceEnvelope]:
        clean = strip_ansi(frame.raw_text)
        lines = clean.splitlines()
        parsed_question = _question(clean)
        answered_question_summaries = _answered_question_summaries(clean)
        trust_dialog = _trust_dialog(lines, parsed_question)
        # A startup trust choice happens to use the same numbered renderer as
        # AskUserQuestion.  It must not become a generic answerable question:
        # trust is its own semantic surface and has distinct policy/replay
        # semantics.  Keep the shared menu shape only in trust evidence.
        question = None if trust_dialog is not None else parsed_question
        resume_picker = _resume_picker(lines)
        agent_manager = _agent_manager(lines)
        diagnostics: list[str] = []
        try:
            transcript = parse_frames("claude_code", [clean])
        except Exception as exc:  # Raw/evidence retention must survive parser defects.
            transcript = {"harness": "claude_code", "state": "unknown", "segments": []}
            diagnostics.append(f"transcript parse failed: {type(exc).__name__}: {exc}")
        tools, agents = [], []
        for segment in transcript.get("segments", []):
            if not isinstance(segment, dict):
                continue
            if segment.get("type") == "tool_call":
                tools.append(
                    {
                        "tool_name": segment.get("title"),
                        "command": segment.get("input"),
                        "output": segment.get("result"),
                        "status": "running" if segment.get("running") else "complete",
                        "elided": bool(segment.get("elided")),
                    }
                )
            elif segment.get("type") == "agent_event":
                agents.append(dict(segment))
        agents.extend(
            {
                "name": match.group(1) or match.group(2),
                "status": "dispatched" if match.group(1) else "completed",
                "source": "frame",
            }
            for match in _AGENT.finditer(clean)
        )
        subagent_transitions: list[dict[str, object]] = []
        if agent_manager is not None:
            prior_statuses = _prior_subagent_statuses(history)
            visible_statuses = {
                str(agent.get("name")): str(agent["status"])
                for agent in agents
                if isinstance(agent, dict) and agent.get("name") and agent.get("status")
            }
            for row in agent_manager["rows"]:
                if not isinstance(row, dict) or row.get("relationship") != "child":
                    continue
                task = str(row.get("task") or "")
                agent = {
                    "name": row["name"],
                    "role": row["name"],
                    "task": row.get("task"),
                    "status": row["status"],
                    "activity": row.get("task"),
                    "elapsed_seconds": row.get("elapsed_seconds"),
                    "token_count": row.get("token_count"),
                    "relationship": "child",
                    "selected": row["selected"],
                    "source": "agent_manager",
                }
                agents.append(agent)
                previous = (
                    prior_statuses.get(task)
                    or prior_statuses.get(str(row["name"]))
                    or visible_statuses.get(task)
                    or visible_statuses.get(str(row["name"]))
                )
                if previous != row["status"]:
                    subagent_transitions.append(
                        {
                            "type": f"claude_code.subagent_{row['status']}",
                            **agent,
                            "previous_status": previous,
                        }
                    )
        permission = None
        if question and _PERMISSION.search(clean):
            text = f"{question['prompt_text']}\n{clean}"
            command = _COMMAND.search(clean)
            permission = {
                "request_id_hint": question["question_id_hint"],
                "tool_name": "Bash" if "bash" in text.lower() else None,
                "command": command.group(1).strip() if command else None,
                "description": question["prompt_text"],
                "choices": question["choices"],
                "selected_choice": next(
                    (item["label"] for item in question["choices"] if item["selected"]), None
                ),
                "risk_attributes": [
                    name
                    for name, pattern in {
                        "shell": r"\b(?:bash|shell|command)\b",
                        "write": r"\b(?:write|edit|delete|rm)\b",
                        "network": r"\b(?:network|http|download)\b",
                    }.items()
                    if re.search(pattern, text, re.I)
                ],
            }
        context_composition = _context_composition(lines)
        context_contributors = (
            context_composition.get("contributors", [])
            if isinstance(context_composition, dict)
            else []
        )
        mcp_servers = [
            {
                "name": contributor["name"],
                "percent_of_usage": contributor["percent_of_usage"],
                "raw_line": next(
                    (
                        line.strip()
                        for line in lines
                        if re.match(
                            rf"^\s*{re.escape(str(contributor['name']))}\s{{2,}}"
                            rf"{contributor['percent_of_usage']:g}%\s*$",
                            line,
                        )
                    ),
                    None,
                ),
            }
            for contributor in context_contributors
            if isinstance(contributor, dict) and contributor.get("category") == "mcp_server"
        ]
        payload: dict[str, object] = {
            # Raw capture provenance is evidence too.  The structured fields
            # below are intentionally a lossy interpretation, so retain enough
            # capture metadata to reprocess a frame with a later parser.
            "raw_frame": {
                "text": frame.raw_text,
                "ansi_preserved": frame.ansi_preserved,
                "width": frame.width,
                "height": frame.height,
                "pane_epoch": frame.pane_epoch,
                "capture_sequence": frame.capture_sequence,
            },
            "header": {
                "version": next((line.strip() for line in lines if "Claude Code v" in line), None),
                "workspace": next(
                    (
                        line.strip()
                        for line in lines
                        if "~/" in line or line.strip().startswith("/")
                    ),
                    None,
                ),
            },
            "composer": _composer(lines),
            "transcript": transcript,
            "question": question,
            "question_history": {"answered_summaries": answered_question_summaries},
            "trust_dialog": trust_dialog,
            "resume_picker": resume_picker,
            "permission": permission,
            "model": _model(clean),
            "usage": asdict(parse_claude_usage_pane(clean)),
            "tool_activity": tools,
            "subagents": agents,
            "agent_manager": agent_manager,
            "subagent_transitions": subagent_transitions,
            "status_lines": [line.strip() for line in lines if _SPINNER.search(line)],
            "context_composition": context_composition,
            "mcp_servers": mcp_servers,
            "notices": [
                line.strip()
                for line in lines
                if re.search(
                    r"\b(?:warning|error|notice|update available|login|trust)\b", line, re.I
                )
            ],
            "surfaces": {
                "trust_dialog": trust_dialog is not None,
                "usage_panel": "current session" in clean.lower()
                and "current week" in clean.lower(),
                "context_panel": context_composition is not None,
                "question_picker": question is not None,
                "permission_dialog": permission is not None,
                "model_picker": bool(_model(clean)["configuration"]["available"]),
                "resume_picker": resume_picker is not None,
            },
        }
        return (
            EvidenceEnvelope(
                evidence_id=f"{frame.frame_id}:claude_code:frame:v2",
                frame_id=frame.frame_id,
                harness_id=frame.harness_id,
                parser_version=self.parser_version,
                captured_at=frame.captured_at,
                evidence_type="claude_code.frame.v2",
                payload=payload,
                source_regions=(ScreenRegionRef("frame"),),
                diagnostics=EvidenceDiagnostics(
                    parser_name="claude_code",
                    messages=("broad renderer-specific payload retained", *diagnostics),
                    unrecognized_regions=(ScreenRegionRef("unclassified_visible_lines"),),
                ),
            ),
        )

    def project_observations(
        self, evidence: Sequence[EvidenceEnvelope], prior: ObservationSnapshot | None
    ) -> ObservationDelta:
        if not evidence:
            return ObservationDelta(updates={}, diagnostics=("no Claude Code evidence",))
        item = evidence[-1]
        payload, ref, at = item.payload, _ref(item), item.captured_at
        rev = prior.revision if prior else ObservationRevision(0, 0, 0)
        surfaces = payload.get("surfaces", {}) if isinstance(payload.get("surfaces"), dict) else {}
        question, permission = payload.get("question"), payload.get("permission")
        model, usage, transcript = (
            payload.get("model", {}),
            payload.get("usage", {}),
            payload.get("transcript", {}),
        )
        primary, modal = SurfaceKind.COMPOSER, None
        for visible, kind, modal_kind in (
            ("permission_dialog", SurfaceKind.PERMISSION_DIALOG, ModalKind.PERMISSION),
            ("trust_dialog", SurfaceKind.TRUST_DIALOG, ModalKind.TRUST),
            ("question_picker", SurfaceKind.QUESTION_PICKER, ModalKind.QUESTION),
            ("resume_picker", SurfaceKind.RESUME_PICKER, ModalKind.RESUME),
            ("model_picker", SurfaceKind.MODEL_PICKER, ModalKind.MODEL_PICKER),
            ("usage_panel", SurfaceKind.USAGE_PANEL, ModalKind.USAGE),
            ("context_panel", SurfaceKind.CONTEXT_PANEL, ModalKind.CONTEXT),
        ):
            if surfaces.get(visible):
                primary, modal = kind, modal_kind
                break
        resume_picker = payload.get("resume_picker")
        resume_rows = (
            resume_picker.get("sessions", []) if isinstance(resume_picker, dict) else []
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
                    primary.name.replace("_", " ").title(),
                    resume_picker.get("selected_ordinal")
                    if modal is ModalKind.RESUME and isinstance(resume_picker, dict)
                    else None,
                    len(resume_rows)
                    if modal is ModalKind.RESUME
                    else len(question.get("choices", []))
                    if isinstance(question, dict)
                    else None,
                    True,
                    True,
                ),
                ref,
                at,
                rev,
            )
            if modal
            else _without(Knowledge.ABSENT, ref, at, rev),
        }
        composer = payload.get("composer")
        if isinstance(composer, dict):
            text = str(composer.get("text") or "")
            updates["composer"] = _present(
                ComposerState(
                    text,
                    " ".join(text.split()),
                    hashlib.sha256(text.encode()).hexdigest() if text else "",
                    True,
                    True,
                    ComposerActionability.ACTIONABLE,
                    bool(composer.get("partial")),
                    bool(composer.get("accepts_submission")),
                ),
                ref,
                at,
                rev,
            )
        else:
            updates["composer"] = _without(Knowledge.UNKNOWN, ref, at, rev)
        spinning = bool(payload.get("status_lines"))
        updates["generation"] = _present(
            GenerationState(
                GenerationPhase.STREAMING if spinning else GenerationPhase.IDLE,
                spinning,
                spinning,
                None,
                None,
                None,
            ),
            ref,
            at,
            rev,
        )
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
        updates["transcript_tail"] = _present(
            TranscriptTailState(
                TurnRef(hashlib.sha256(user_text.encode()).hexdigest()[:16], "user")
                if user
                else None,
                TurnRef(hashlib.sha256(assistant_text.encode()).hexdigest()[:16], "assistant")
                if assistant
                else None,
                tuple(hashlib.sha256(str(s.get("text", "")).encode()).hexdigest() for s in users),
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
        )
        updates["question"] = self._question_observed(question, ref, at, rev)
        updates["permission_request"] = self._permission_observed(permission, ref, at, rev)
        active = model.get("active", {}) if isinstance(model, dict) else {}
        updates["active_model"] = (
            _present(
                ModelState(
                    str(active["model_id"]),
                    active.get("effort"),
                    active.get("display_name"),
                    active.get("provider"),
                ),
                ref,
                at,
                rev,
            )
            if isinstance(active, dict) and active.get("model_id")
            else _without(Knowledge.UNKNOWN, ref, at, rev)
        )
        config = model.get("configuration", {}) if isinstance(model, dict) else {}
        updates["model_configuration"] = (
            _present(
                ModelConfigurationState(
                    _choice_states(config.get("available")),
                    config.get("highlighted_model_id"),
                    config.get("selected_model_id"),
                    config.get("configured_model_id"),
                    config.get("pending_changes"),
                    tuple(tuple(row) for row in config.get("parameters", [])),
                ),
                ref,
                at,
                rev,
            )
            if isinstance(config, dict) and config.get("available")
            else _without(Knowledge.UNKNOWN, ref, at, rev)
        )
        windows = (
            tuple(
                UsageWindow(
                    str(row.get("name")), row.get("percent_used"), None, row.get("reset_at")
                )
                for row in usage.get("windows", [])
                if isinstance(row, dict)
            )
            if isinstance(usage, dict)
            else ()
        )
        updates["usage"] = (
            _present(
                UsageState(
                    None,
                    usage.get("plan"),
                    windows,
                    "current",
                    primary if primary is SurfaceKind.USAGE_PANEL else None,
                    None,
                    usage.get("session"),
                ),
                ref,
                at,
                rev,
            )
            if windows
            else _without(Knowledge.UNKNOWN, ref, at, rev)
        )
        tools = payload.get("tool_activity", [])
        interactions = tuple(
            ToolInteraction(
                t.get("tool_name"), t.get("command"), (), (), t.get("status"), None, None
            )
            for t in tools
            if isinstance(t, dict)
        )
        updates["tool_activity"] = _present(
            ToolActivityState(
                tuple(t for t in interactions if t.status == "running"),
                tuple(t for t in interactions if t.status != "running"),
            ),
            ref,
            at,
            rev,
        )
        events = tuple(
            {"type": "claude_code.subagent", **agent}
            for agent in payload.get("subagents", [])
            if isinstance(agent, dict)
        )
        events += tuple(
            transition
            for transition in payload.get("subagent_transitions", [])
            if isinstance(transition, dict)
        )
        question_history = payload.get("question_history", {})
        if isinstance(question_history, dict):
            events += tuple(
                {"type": "claude_code.question_answered", "summary": str(summary)}
                for summary in question_history.get("answered_summaries", [])
            )
        trust_dialog = payload.get("trust_dialog")
        if isinstance(trust_dialog, dict):
            events += (
                {
                    "type": "claude_code.trust_dialog_visible",
                    "workspace": trust_dialog.get("workspace"),
                    "selected_choice": trust_dialog.get("selected_choice"),
                },
            )
        if isinstance(resume_picker, dict):
            pagination = resume_picker.get("pagination", {})
            events += (
                {
                    "type": "claude_code.resume_picker_visible",
                    "visible_count": len(resume_rows),
                    "pagination_current": pagination.get("current"),
                    "pagination_total": pagination.get("total"),
                    "empty": bool(resume_picker.get("empty")),
                },
            )
        return ObservationDelta(
            updates=updates,
            evidence_refs=(ref,),
            semantic_events=events,
            diagnostics=item.diagnostics.messages,
        )

    @staticmethod
    def _question_observed(payload, ref, at, rev):
        if not isinstance(payload, dict):
            return _without(Knowledge.ABSENT, ref, at, rev)
        return _present(
            QuestionState(
                payload.get("question_id_hint"),
                payload.get("prompt_text"),
                _choice_states(payload.get("choices")),
                payload.get("selection_mode"),
                payload.get("active_tab"),
                tuple(payload.get("visible_tabs", [])),
                payload.get("allow_custom_answer"),
                payload.get("custom_answer_text"),
                payload.get("submit_label"),
                payload.get("decline_label"),
                tuple(payload.get("answered_summary", [])),
            ),
            ref,
            at,
            rev,
        )

    @staticmethod
    def _permission_observed(payload, ref, at, rev):
        if not isinstance(payload, dict):
            return _without(Knowledge.ABSENT, ref, at, rev)
        return _present(
            PermissionRequestState(
                payload.get("request_id_hint"),
                payload.get("tool_name"),
                payload.get("command"),
                payload.get("description"),
                _choice_states(payload.get("choices")),
                payload.get("selected_choice"),
                frozenset(payload.get("risk_attributes", [])),
            ),
            ref,
            at,
            rev,
        )

    def lower(  # noqa: PLR0911 - one branch per semantic action is intentional
        self, action: SemanticAction, snapshot: ObservationSnapshot
    ) -> Sequence[TerminalEffect]:
        if isinstance(action, InsertPromptPayload):
            return tuple(
                SendLiteralKeys(str(uuid4()), chunk.text, FAST_HUMANIZED_TYPING)
                if chunk.provenance is InputProvenance.USER_TYPED
                else PasteBuffer(str(uuid4()), chunk.text)
                for chunk in action.chunks
            )
        if isinstance(action, ClearComposer):
            return (SendNamedKey(str(uuid4()), "C-u"),)
        if isinstance(action, CommitPromptSubmission):
            return (SendNamedKey(str(uuid4()), "Enter"),)
        if isinstance(action, RequestUsage):
            return (
                SendNamedKey(f"{action.action_id}:dismiss", "Escape"),
                SendLiteralKeys(f"{action.action_id}:usage", "/usage"),
                SendNamedKey(f"{action.action_id}:open-usage", "Enter"),
            )
        if isinstance(action, (DismissOverlay, RestoreComposer, SendInterrupt)):
            return (SendNamedKey(str(uuid4()), "Escape"),)
        if isinstance(action, AnswerQuestion):
            return _lower_question(action, snapshot.question)
        if isinstance(action, AnswerPermission):
            return _lower_choice(
                action.response_id, action.response_label, None, snapshot.permission_request
            )
        if isinstance(action, SelectModel):
            return _lower_model_selection(action, snapshot)
        if isinstance(action, OpenModelPicker):
            return _open_model_picker(action, snapshot)
        return ()


_EFFORT_ORDER = ("low", "medium", "high", "max")


def _lower_model_selection(
    action: SelectModel, snapshot: ObservationSnapshot
) -> Sequence[TerminalEffect]:
    """Lower only from picker state that is actually observed.

    Opening the picker is a replay-safe navigation step.  Selecting a row,
    setting its default, and activating it for the current session remain one
    recorded, ambiguous semantic action; this function merely encodes Claude
    Code's physical controls.  It neither polls nor assumes that either
    confirmation changed the running model.
    """

    if action.provider not in {None, "anthropic"}:
        raise ValueError("Claude Code only accepts an anthropic model target")
    unsupported = {
        "context_mode": action.context_mode,
        "fast_enabled": action.fast_enabled,
        "max_mode_enabled": action.max_mode_enabled,
        "thinking_enabled": action.thinking_enabled,
        "run_mode": action.run_mode,
    }
    names = [name for name, value in unsupported.items() if value is not None]
    if names:
        raise ValueError(
            "Claude Code model lowering has no observed controls for " + ", ".join(names)
        )

    observed = snapshot.model_configuration
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        raise ValueError("Claude Code model selection requires current picker evidence")

    config = observed.value
    target_index = next(
        (
            index
            for index, choice in enumerate(config.available)
            if choice.stable_choice_id == action.model_id
        ),
        None,
    )
    if target_index is None:
        raise ValueError("requested Claude Code model is not visible in the observed picker")
    target = config.available[target_index]
    if target.disabled is True:
        raise ValueError("requested Claude Code model is disabled in the observed picker")
    current_index = next(
        (index for index, choice in enumerate(config.available) if choice.highlighted is True),
        next(
            (
                index
                for index, choice in enumerate(config.available)
                if choice.selected is True or choice.current is True
            ),
            0,
        ),
    )
    effects: list[TerminalEffect] = []
    key = "Down" if target_index >= current_index else "Up"
    effects.extend(
        SendNamedKey(f"{action.action_id}:model-nav:{index}", key)
        for index in range(abs(target_index - current_index))
    )

    parameters = dict(config.parameters)
    configured = config.configured_model_id == action.model_id
    effort_matches = action.effort is None or parameters.get("effort") == action.effort
    if configured and effort_matches:
        # Claude exposes a distinct `s` action to apply the saved/default
        # configuration to this session.  The controller verifies the later
        # header readback; selection evidence alone is never treated as active.
        effects.append(SendNamedKey(f"{action.action_id}:use-current-session", "s"))
        return tuple(effects)

    if action.effort is not None:
        current_effort = parameters.get("effort")
        if current_effort not in _EFFORT_ORDER:
            raise ValueError("Claude Code effort control is not visible in the observed picker")
        if action.effort not in _EFFORT_ORDER:
            raise ValueError(f"unsupported Claude Code effort {action.effort!r}")
        current_effort_index = _EFFORT_ORDER.index(current_effort)
        target_effort_index = _EFFORT_ORDER.index(action.effort)
        effort_key = "Right" if target_effort_index >= current_effort_index else "Left"
        effects.extend(
            SendNamedKey(f"{action.action_id}:effort:{index}", effort_key)
            for index in range(abs(target_effort_index - current_effort_index))
        )
    effects.append(SendNamedKey(f"{action.action_id}:set-default", "Enter"))
    return tuple(effects)


def _open_model_picker(
    action: OpenModelPicker, snapshot: ObservationSnapshot
) -> Sequence[TerminalEffect]:
    if snapshot.surface.knowledge is not Knowledge.PRESENT or snapshot.surface.value is None:
        raise ValueError("Claude Code model picker requires a known safe surface")
    if snapshot.surface.value.primary not in {SurfaceKind.COMPOSER, SurfaceKind.TRANSCRIPT}:
        raise ValueError("Claude Code model picker will not replace an unobserved overlay")
    return (
        SendLiteralKeys(f"{action.action_id}:open", "/model", FAST_HUMANIZED_TYPING),
        SendNamedKey(f"{action.action_id}:open-enter", "Enter"),
    )


def _lower_choice(choice_id, label, custom, observed):
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        return ()
    choices = observed.value.choices
    target = next(
        (
            choice
            for choice in choices
            if choice.stable_choice_id == choice_id or choice.label == label
        ),
        None,
    )
    if target is None and custom is None:
        return ()
    current = next(
        (i for i, choice in enumerate(choices) if choice.highlighted or choice.selected), 0
    )
    target_index = choices.index(target) if target else current
    key = "Down" if target_index >= current else "Up"
    effects = [SendNamedKey(str(uuid4()), key) for _ in range(abs(target_index - current))]
    if custom is not None:
        effects.append(SendLiteralKeys(str(uuid4()), custom, FAST_HUMANIZED_TYPING))
    return tuple([*effects, SendNamedKey(str(uuid4()), "Enter")])


def _lower_question(action: AnswerQuestion, observed) -> Sequence[TerminalEffect]:
    """Lower a semantic answer from current menu evidence, never row guesses.

    Claude's multi-select menus use Space to check each target and Enter to
    submit.  A shared multi-answer action therefore lowers each semantic choice
    independently instead of discarding all but the first selection.
    """

    if action.mode is QuestionAnswerMode.DECLINE:
        return _lower_choice(None, "Chat about this", None, observed)
    if action.mode is not QuestionAnswerMode.MULTIPLE:
        selection = action.selections[0] if action.selections else None
        return _lower_choice(
            selection.stable_choice_id if selection else None,
            selection.label if selection else None,
            action.custom_answer,
            observed,
        )
    if observed.knowledge is not Knowledge.PRESENT or observed.value is None:
        return ()
    choices = observed.value.choices
    cursor = next(
        (index for index, choice in enumerate(choices) if choice.highlighted or choice.selected), 0
    )
    effects: list[TerminalEffect] = []
    for selection in action.selections:
        target_index = next(
            (
                index
                for index, choice in enumerate(choices)
                if choice.stable_choice_id == selection.stable_choice_id
                or choice.label == selection.label
            ),
            None,
        )
        if target_index is None:
            return ()
        key = "Down" if target_index >= cursor else "Up"
        effects.extend(SendNamedKey(str(uuid4()), key) for _ in range(abs(target_index - cursor)))
        if choices[target_index].checked is not True:
            effects.append(SendNamedKey(str(uuid4()), "Space"))
        cursor = target_index
    effects.append(SendNamedKey(str(uuid4()), "Enter"))
    return tuple(effects)


__all__ = ["ClaudeCodeAdapter"]
