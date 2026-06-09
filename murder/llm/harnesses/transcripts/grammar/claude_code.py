"""Claude Code harness grammar plugin."""

from __future__ import annotations

import re
from typing import Any

from murder.llm.harnesses.transcripts.segments import (
    ChoiceOptionDict,
    ChoicePromptSegment,
    Segment,
)
from murder.llm.harnesses.transcripts._shared import (
    _RULE_RE,
    _dedupe_adjacent,
    truncate_title,
    reflow_paragraphs,
)

# ---- claude_code regexes --------------------------------------------------- #
_CC_PROMPT_RE = re.compile(r"^\s*❯[\s ]*(.*)$")
_CC_CHOICE_OPTION_PROMPT_RE = re.compile(r"^\s*❯[\s\xa0]*\d+\.\s+")
_CC_BULLET_RE = re.compile(r"^●\s+(.*)$")
# A completion marker's tail is a *duration* (`3m 59s`, `45s`, `1h 2m`), not
# arbitrary text. Constraining the capture to a duration shape keeps spinner
# status lines like `✻ Waiting for 1 background agent to finish` from being
# misread as final-phase elapsed markers.
_CC_COMPLETION_RE = re.compile(
    r"^\s*[✻✶✳✽✢]\s+[A-Z][\w-]+\s+for\s+(\d+\s*[hms](?:\s+\d+\s*[hms])*)\s*$"
)
_CC_AGENT_DONE_RE = re.compile(r'^●\s+Agent\s+"(.+?)"\s+completed\s+·\s+(.+?)\s*$')
_CC_AGENT_START_RE = re.compile(r"^●\s+Agent\((.+?)\)\s*$")
_CC_TOOL_RE = re.compile(r"^●\s+([A-Z][a-zA-Z]+)\((.*)$")
_CC_SUMMARY_RE = re.compile(
    r"^\s+(Searched|Searching|Read|Reading|Wrote|Writing|Edited|Editing|Listed|Listing"
    r"|Found|Fetched|Fetching)\b.*"
)
_CC_RUNNING_SUMMARY_RE = re.compile(
    r"^(Searching|Searched|Reading|Read|Writing|Wrote|Editing|Edited|Listing|Listed"
    r"|Finding|Found|Fetching|Fetched)\b.*…"
)
_CC_SPINNER_RE = re.compile(
    r"^\s*[·*✻✶✳✽✢⠁-⣿◐◓◑◒]?\s*[A-Z][\w-]+…+\s*(?:\([^)]*(?:tokens|thought|↑|↓|esc to)[^)]*\))?\s*$"
)
_CC_SHELL_PROMPT_RE = re.compile(r"^\w+@\w[-\w.]*:[~\w/]*\s*\$\s")
_CC_AGENT_ROSTER_RE = re.compile(r"^\s*[●◯]\s+(?:main|general-purpose)\b")
_CC_UNCACHED_NOTICE_RE = re.compile(
    r"(?:~?\d[\d.,]*(?:\s*[kKmM])?(?:\s+tokens)?)\s+uncached\b"
    r"(?:\s+·\s+/clear to start fresh)?",
    re.IGNORECASE,
)
_CC_RESULT_RE = re.compile(r"^\s*⎿\s?(.*)$")
_CC_ELIDED_RE = re.compile(r"…\s*\+\d+\s+lines")


def _is_live_prompt(lines: list[str], index: int) -> bool:
    """A `❯` line is the live input box when it sits between two horizontal rules."""
    before = index - 1
    while before >= 0 and not lines[before].strip():
        before -= 1
    after = index + 1
    while after < len(lines) and not lines[after].strip():
        after += 1
    return (
        before >= 0
        and after < len(lines)
        and bool(_RULE_RE.match(lines[before]))
        and bool(_RULE_RE.match(lines[after]))
    )


def _cc_is_chrome(line: str) -> bool:
    stripped = line.strip()
    prompt_m = _CC_PROMPT_RE.match(line)
    return bool(
        not stripped
        or _RULE_RE.match(line)
        or _CC_SPINNER_RE.match(line)
        or _CC_SHELL_PROMPT_RE.match(line)
        or _CC_AGENT_ROSTER_RE.match(line)
        or (prompt_m is not None and not (prompt_m.group(1) or "").strip())
        or "bypass permissions" in line
        or "esc to interrupt" in line
        or "shift+tab to cycle" in line
        or _CC_UNCACHED_NOTICE_RE.search(line)
        or "/clear to start fresh" in line
        or "↑/↓ to " in line
        or "to manage" in line
        or "Backgrounded agent" in stripped
        or stripped.startswith("Tip:")
        or (stripped.startswith("⎿") and "Tip:" in stripped)
        or stripped.startswith(("▐", "▝", "▘", "▛", "▜"))
        or "Claude Code v" in line
        or "Waiting for" in stripped
    )


def _cc_starts_block(lines: list[str], index: int) -> bool:
    line = lines[index]
    return bool(
        (_CC_PROMPT_RE.match(line) and not _is_live_prompt(lines, index))
        or _CC_BULLET_RE.match(line)
        or _CC_COMPLETION_RE.match(line)
        or _CC_SUMMARY_RE.match(line)
    )


_CC_BANNER_GLYPHS = ("▐", "▝", "▘", "▛", "▜")


def _cc_clip_preamble(lines: list[str]) -> list[str]:
    """Drop everything up to and including the startup banner.

    The box-drawing logo + ``Claude Code v…`` banner is rendered once at launch
    and, because scrollback only ever grows, persists at the top of the captured
    pane for the session's life. Anything *above* it is pre-conversation noise:
    a shell prompt and the (often line-wrapped) launch command when claude
    wasn't the pane's initial command, a resize redraw, an MOTD. Those un-indented
    lines would otherwise be swept up by the bare-prose branch as a phantom
    assistant turn. Clip to just after the banner so the conversation region is
    clean. If no banner is present (we attached mid-session, or it never rendered),
    keep everything — there is no preamble to strip.
    """
    last_banner = -1
    for idx, line in enumerate(lines):
        if _cc_starts_block(lines, idx):
            break  # conversation has begun; the banner is behind us
        if "Claude Code v" in line or line.strip().startswith(_CC_BANNER_GLYPHS):
            last_banner = idx
    return lines[last_banner + 1 :] if last_banner >= 0 else lines


def _strip_expand_hint(text: str) -> str:
    return re.sub(r"\s*\(ctrl\+[ot][^)]*\)\s*$", "", text).rstrip()


def _dedent_cc(line: str) -> str:
    if line.startswith("  "):
        return line[2:].rstrip()
    return line.rstrip()


def _reflow_prose(lines: list[str]) -> str:
    return reflow_paragraphs(
        lines,
        dedent=_dedent_cc,
        preserve_prefixes=("┌", "│", "├", "└", "┘", "- ", "* "),
        preserve_strip=False,
    )


def _reflow_user(lines: list[str]) -> str:
    cleaned = [_dedent_cc(line) for line in lines]
    return " ".join(line.strip() for line in cleaned if line.strip())


def _cc_collect_result(lines: list[str], i: int) -> tuple[str | None, bool, int]:
    result_lines: list[str] = []
    elided = False
    while i < len(lines):
        line = lines[i]
        if _cc_starts_block(lines, i):
            break
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        m = _CC_RESULT_RE.match(line)
        if m:
            body = m.group(1)
            if _CC_ELIDED_RE.search(body) or body.strip().startswith("…"):
                elided = True
            elif body.strip():
                result_lines.append(_strip_expand_hint(body).rstrip())
            i += 1
            continue
        if line.startswith("  "):
            if _CC_ELIDED_RE.search(stripped) or stripped.startswith("…"):
                elided = True
            else:
                result_lines.append(_strip_expand_hint(stripped))
            i += 1
            continue
        break
    result = "\n".join(result_lines).strip() or None
    return result, elided, i


def parse_lines(
    lines: list[str],
    system_prompt: str | None = None,  # noqa: ARG001
    user_texts: list[str] | None = None,  # noqa: ARG001
) -> list[Segment]:
    segments: list[Segment] = []
    lines = _cc_clip_preamble(lines)
    i = 0
    while i < len(lines):
        line = lines[i]

        prompt = _CC_PROMPT_RE.match(line)
        if (
            prompt
            and prompt.group(1).strip()
            and not _is_live_prompt(lines, i)
            and not _CC_CHOICE_OPTION_PROMPT_RE.match(line)
        ):
            body = [prompt.group(1)]
            i += 1
            while i < len(lines):
                cur = lines[i]
                if _cc_starts_block(lines, i):
                    break
                if cur.startswith("  ") and not _cc_is_chrome(cur):
                    body.append(cur)
                    i += 1
                elif not cur.strip():
                    # Cross blank lines only when more indented continuation follows
                    j = i + 1
                    while j < len(lines) and not lines[j].strip():
                        j += 1
                    if j < len(lines) and lines[j].startswith("  ") and not _cc_starts_block(lines, j):
                        body.append("")
                        i += 1
                    else:
                        break
                else:
                    break
            segments.append({"type": "user", "text": _reflow_user(body)})
            continue

        done = _CC_AGENT_DONE_RE.match(line)
        if done:
            segments.append(
                {
                    "type": "agent_event",
                    "name": done.group(1),
                    "status": "completed",
                    "elapsed": done.group(2),
                }
            )
            i += 1
            continue

        start = _CC_AGENT_START_RE.match(line)
        if start:
            segments.append(
                {
                    "type": "agent_event",
                    "name": start.group(1),
                    "status": "dispatched",
                    "elapsed": None,
                }
            )
            i += 1
            while i < len(lines) and not _cc_starts_block(lines, i):
                i += 1
            continue

        completion = _CC_COMPLETION_RE.match(line)
        if completion:
            for segment in reversed(segments):
                if segment["type"] == "assistant":
                    segment["phase"] = "final"
                    segment["elapsed"] = completion.group(1)
                    break
            i += 1
            continue

        tool = _CC_TOOL_RE.match(line)
        if tool:
            verb = tool.group(1)
            body = tool.group(2)
            i += 1
            while (
                i < len(lines)
                and not _cc_starts_block(lines, i)
                and not _CC_RESULT_RE.match(lines[i])
                and lines[i].strip()
                and not _cc_is_chrome(lines[i])
            ):
                body += " " + lines[i].strip()
                i += 1
            command = re.sub(r"\)\s*$", "", body)
            command = _strip_expand_hint(re.sub(r"\s+", " ", command).strip())
            result, elided, i = _cc_collect_result(lines, i)
            segments.append(
                {
                    "type": "tool_call",
                    "title": truncate_title(command),
                    "input": truncate_title(command) if verb == "Bash" else None,
                    "result": result,
                    "elided": elided or result is None,
                    "running": False,
                }
            )
            continue

        if _CC_SUMMARY_RE.match(line) and not _CC_BULLET_RE.match(line):
            if _CC_RUNNING_SUMMARY_RE.match(line.strip()):
                i += 1
                _, _, i = _cc_collect_result(lines, i)
                continue
            title = _strip_expand_hint(line.strip())
            i += 1
            result, elided, i = _cc_collect_result(lines, i)
            segments.append(
                {
                    "type": "tool_call",
                    "title": truncate_title(title),
                    "input": None,
                    "result": result,
                    "elided": elided or result is None,
                    "running": False,
                }
            )
            continue

        bullet = _CC_BULLET_RE.match(line)
        if bullet and _CC_RUNNING_SUMMARY_RE.match(bullet.group(1).strip()):
            i += 1
            _, _, i = _cc_collect_result(lines, i)
            continue

        if bullet:
            body = [bullet.group(1)]
            i += 1
            while i < len(lines) and not _cc_starts_block(lines, i):
                if not _cc_is_chrome(lines[i]):
                    body.append(lines[i])
                elif not lines[i].strip():
                    body.append("")
                i += 1
            text = _reflow_prose(body)
            if text:
                segments.append(
                    {
                        "type": "assistant",
                        "phase": "intermediate",
                        "text": text,
                        "elapsed": None,
                    }
                )
            continue

        if (
            not _cc_is_chrome(line)
            and not (_CC_PROMPT_RE.match(line) and _is_live_prompt(lines, i))
            and not _CC_CHOICE_OPTION_PROMPT_RE.match(line)
            and not line.startswith(" ")
        ):
            body = [line]
            i += 1
            while i < len(lines) and not _cc_starts_block(lines, i):
                cur = lines[i]
                if _CC_PROMPT_RE.match(cur) and _is_live_prompt(lines, i):
                    break
                if _CC_CHOICE_OPTION_PROMPT_RE.match(cur):
                    break
                if cur.startswith(" ") and cur.strip():
                    break
                if not _cc_is_chrome(cur):
                    body.append(cur)
                elif not cur.strip():
                    body.append("")
                i += 1
            text = _reflow_prose(body)
            if text:
                segments.append(
                    {
                        "type": "assistant",
                        "phase": "intermediate",
                        "text": text,
                        "elapsed": None,
                    }
                )
            continue

        i += 1
    return _dedupe_adjacent(segments)


def choice_prompt_segment(prompt: Any) -> ChoicePromptSegment:
    """Convert a MultipleChoicePrompt into a ChoicePromptSegment dict."""
    options: list[ChoiceOptionDict] = [
        {
            "number": option.number,
            "label": option.label,
            "description": option.description or None,
        }
        for option in prompt.options
    ]
    return {
        "type": "choice_prompt",
        "question": prompt.question,
        "options": options,
        "footer": prompt.footer or None,
        "selected": prompt.selected_option.number,
        "answered": False,
        "chosen": None,
    }


def is_idle(pane_text: str) -> bool:
    """True when the CC pane is awaiting input."""
    from murder.llm.harnesses.claude_code import ClaudeCodeAdapter  # noqa: PLC0415

    return ClaudeCodeAdapter().is_idle(pane_text)


def detect_live_choice_prompt(frame: str) -> Any | None:
    """Return a live MultipleChoicePrompt if CC is showing a choice, else None."""
    from murder.llm.harnesses.choice_prompt import parse_claude_code_choice_prompt  # noqa: PLC0415
    from murder.llm.harnesses.parsing import strip_ansi  # noqa: PLC0415

    return parse_claude_code_choice_prompt(strip_ansi(frame))


def close_last_turn(segments: list[Segment]) -> None:
    """CC uses inline completion markers — no phase fixup needed at idle."""
    pass  # no-op: CC emits _CC_COMPLETION_RE inline
