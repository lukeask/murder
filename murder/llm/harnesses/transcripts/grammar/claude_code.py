"""Claude Code harness grammar plugin."""

from __future__ import annotations

import re
from typing import Any

from murder.llm.harnesses.transcripts.segments import (
    ChoiceOptionDict,
    ChoicePromptSegment,
    Segment,
    SpannedSegment,
)
from murder.llm.harnesses.transcripts._shared import (
    dedupe_adjacent_spanned,
    truncate_title,
    reflow_paragraphs,
)
from murder.llm.harnesses.transcripts.toolkit import (
    BASE_CHROME_RULES,
    attribute_completion,
    chrome_matcher,
    is_rule_sandwiched,
    record_dropped_completion,
    regex_match_rule,
    regex_search_rule,
    stripped_startswith_rule,
    stripped_substring_rule,
    substring_rule,
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
# The status text after the spinner glyph(s) is usually a single gerund
# (``Cogitating…``) but newer CC builds emit a contextual multi-word phrase
# (``Updating sizing and tests…``); the inner spaces broke the old single-token
# ``[A-Z][\w-]+`` match, leaking dozens of near-identical animation frames as
# phantom assistant prose. Allow the phrase to span words, and require ≥1 leading
# glyph (every real spinner frame has one — incl. the dim ``·`` frame) so a plain
# assistant sentence ending in ``…`` can't be mistaken for chrome. The gerund
# class also allows an apostrophe: CC's whimsical word list includes elided forms
# (``Beboppin'``, ``Jivin'``) whose ``'`` is outside ``\w`` — without it every
# animation frame of those words leaked as a phantom assistant turn.
_CC_SPINNER_RE = re.compile(
    r"^\s*(?:[·*✻✶✳✽✢⠁-⣿◐◓◑◒]\s*)+[A-Z][\w'’-]+(?:[ \t][\w'’-]+)*…+\s*"
    r"(?:\([^)]*(?:tokens|thought|thinking|effort|↑|↓|esc to)[^)]*\))?\s*$"
)
_CC_SHELL_PROMPT_RE = re.compile(r"^\w+@\w[-\w.]*:[~\w/]*\s*\$\s")
# The background-agent spinner (`✻ Waiting for 1 background agent to finish`).
# Anchored to the `N background agent(s)` shape so a real continuation line
# like "Waiting for your reply…" isn't eaten as chrome.
_CC_WAITING_AGENTS_RE = re.compile(
    r"^\s*[·*✻✶✳✽✢⠁-⣿◐◓◑◒]?\s*Waiting for\s+\d+\s+background\s+agents?\b"
)
_CC_AGENT_ROSTER_RE = re.compile(r"^\s*[●◯]\s+(?:main|general-purpose)\b")
_CC_UNCACHED_NOTICE_RE = re.compile(
    r"(?:~?\d[\d.,]*(?:\s*[kKmM])?(?:\s+tokens)?)\s+uncached\b"
    r"(?:\s+·\s+/clear to start fresh)?",
    re.IGNORECASE,
)
_CC_RESULT_RE = re.compile(r"^\s*⎿\s?(.*)$")
# The AskUserQuestion dialog's tab header (`←  ☐ Toppings  ✔ Submit  →`) —
# dialog chrome, never assistant prose. Rendered while a (multi-)select
# question is live; the question/options are carried by the choice_prompt
# segment, so this line must not leak into an assistant block.
_CC_DIALOG_TAB_RE = re.compile(r"^\s*←\s+.*\s+→\s*$")
_CC_ELIDED_RE = re.compile(r"…\s*\+\d+\s+lines")
# A lone ``●`` with no trailing text is CC's live "responding" indicator dot, not
# a bullet turn — ``_CC_BULLET_RE`` requires content after the glyph, so a bare
# ``●`` matches no other rule and falls through to the bare-prose branch, leaking
# as a phantom assistant segment containing just ``●``.
_CC_BARE_BULLET_RE = re.compile(r"^\s*●\s*$")
# A `❯` prompt whose body is just a slash-command echo (`/model opus`, `/clear`)
# is CC chrome, not a user turn — the harness echoes the command into the prompt
# box but the user never "said" it to the model. Suppress it (the old parsing.py
# did the same via this regex). Only CC needs it today; keep it local.
_SLASH_COMMAND_RE = re.compile(r"/[A-Za-z][\w-]*(?:\s+.*)?\Z")
# /usage modal rows — session/week bars, reset prose, and the boxed overlay
# scrollback that persists above a fresh modal. Indented rows are otherwise
# absorbed into an in-flight ● assistant block when projection races the modal.
_CC_USAGE_SESSION_HDR_RE = re.compile(r"^\s*Current session\s*$", re.IGNORECASE)
_CC_USAGE_WEEK_HDR_RE = re.compile(r"^\s*Current week\b", re.IGNORECASE)
# Require a bar glyph or a wide leading gap so prose like "only 5% used so far"
# (no bar, short prefix) is never swallowed.
_CC_USAGE_PERCENT_RE = re.compile(
    r"^\s*(?:[█░▌\[\]=]+|\s{8,})\s*\d+(?:\.\d+)?%\s+used\s*$",
    re.IGNORECASE,
)
_CC_USAGE_RESET_RE = re.compile(r"^\s*Resets?\s+", re.IGNORECASE)
_CC_USAGE_BOX_RE = re.compile(
    r"^[│╭╰╮╯├└┘┌┐].*(?:% used|Current session|Current week|Usage:|Claude Code|"
    r"Resets?\s|cache read|cache write|\d+\s+input,)",
    re.IGNORECASE,
)
_CC_USAGE_BOX_FRAME_RE = re.compile(r"^[╭╰╮╯├└┘┌┐][─═│\s]*[╭╰╮╯├└┘┌┐│]?\s*$")
_CC_USAGE_BOX_PADDING_RE = re.compile(r"^\s*│[│\s]*│\s*$")
_CC_USAGE_PLAIN_RE = re.compile(r"^\s*/usage\s*$")
_CC_USAGE_TOKEN_ROW_RE = re.compile(
    r"Usage:\s+\d+\s+input,\s*\d+\s+output,",
    re.IGNORECASE,
)


def _is_live_prompt(lines: list[str], index: int) -> bool:
    """A `❯` line is the live input box when it sits between two horizontal rules."""
    return is_rule_sandwiched(lines, index)


def _cc_empty_prompt(line: str) -> bool:
    """A bare ``❯`` with nothing typed after it — the resting input box."""
    prompt_m = _CC_PROMPT_RE.match(line)
    return prompt_m is not None and not (prompt_m.group(1) or "").strip()


def _cc_result_tip(line: str) -> bool:
    """A ``⎿`` result row that is actually a ``Tip:`` hint, not tool output."""
    s = line.strip()
    return s.startswith("⎿") and "Tip:" in s


# CC chrome: shared base (blank + rule) plus CC's own status bars, spinners,
# shell prompts, banners and one-off hint substrings, as composable rules.
_cc_is_chrome = chrome_matcher(
    *BASE_CHROME_RULES,
    regex_match_rule(_CC_SPINNER_RE),
    regex_match_rule(_CC_BARE_BULLET_RE),
    regex_match_rule(_CC_SHELL_PROMPT_RE),
    regex_match_rule(_CC_AGENT_ROSTER_RE),
    _cc_empty_prompt,
    substring_rule(
        "bypass permissions",
        "esc to interrupt",
        "shift+tab to cycle",
        "/clear to start fresh",
        "↑/↓ to ",
        "↓ to manage",
        "Claude Code v",
    ),
    regex_search_rule(_CC_UNCACHED_NOTICE_RE),
    regex_match_rule(_CC_WAITING_AGENTS_RE),
    regex_match_rule(_CC_DIALOG_TAB_RE),
    regex_match_rule(_CC_USAGE_SESSION_HDR_RE),
    regex_match_rule(_CC_USAGE_WEEK_HDR_RE),
    regex_match_rule(_CC_USAGE_PERCENT_RE),
    regex_match_rule(_CC_USAGE_RESET_RE),
    regex_match_rule(_CC_USAGE_BOX_RE),
    regex_match_rule(_CC_USAGE_BOX_FRAME_RE),
    regex_match_rule(_CC_USAGE_BOX_PADDING_RE),
    regex_match_rule(_CC_USAGE_PLAIN_RE),
    regex_search_rule(_CC_USAGE_TOKEN_ROW_RE),
    stripped_substring_rule("Backgrounded agent"),
    stripped_startswith_rule("Tip:", "▐", "▝", "▘", "▛", "▜"),
    stripped_substring_rule(
        "What's contributing to your limits usage?",
        "Claude Code – Usage",
        "Total cost:",
        "Total duration (API):",
        "Total duration (wall):",
        "Total code changes:",
        "Settings  Status   Config   Usage   Stats",
        "Approximate, based on local sessions",
        "Last 24h · these are independent",
        "% of your usage came from subagent-heavy sessions",
        "Subagents               % of usage",
        "MCP servers             % of usage",
        "d to day · w to week",
        "Scanning local sessions",
        "Usage credits are off",
        "Esc to cancel",
    ),
    _cc_result_tip,
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


def _cc_clip_preamble(lines: list[str]) -> tuple[list[str], int]:
    """Drop everything up to and including the startup banner.

    Returns the clipped lines and the absolute index of the first kept line
    (the offset that maps clipped-coordinate spans back to scrollback coords).

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
    if last_banner >= 0:
        return lines[last_banner + 1 :], last_banner + 1
    return lines, 0


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
    # Delegate to the shared classifier so a user turn that carries code / tables /
    # lists keeps its structure instead of being crushed to one line.
    return _reflow_prose(lines)


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
    system_prompt: str | None = None,
    user_texts: list[str] | None = None,
) -> list[Segment]:
    return [s.segment for s in parse_spanned(lines, system_prompt, user_texts)]


def parse_spanned(
    lines: list[str],
    system_prompt: str | None = None,  # noqa: ARG001
    user_texts: list[str] | None = None,  # noqa: ARG001
) -> list[SpannedSegment]:
    """Span-annotated parse: each segment carries the absolute scrollback line
    range it was built from. ``parse_lines`` is the span-stripped projection."""
    spanned: list[SpannedSegment] = []
    clipped, base = _cc_clip_preamble(lines)

    def emit(seg: Segment, start: int, end: int) -> None:
        spanned.append(SpannedSegment(seg, base + start, base + end))

    lines = clipped
    i = 0
    while i < len(lines):
        line = lines[i]
        block_start = i

        prompt = _CC_PROMPT_RE.match(line)
        if (
            prompt
            and prompt.group(1).strip()
            and not _is_live_prompt(lines, i)
            and not _CC_CHOICE_OPTION_PROMPT_RE.match(line)
        ):
            # A `❯` prompt echoing a slash-command (`/model opus`, `/clear`) is CC
            # chrome, not a user turn. Suppressing only the user branch is not
            # enough: the line would fall through to the bare-prose branch below and
            # re-emerge as a phantom assistant segment (text incl. the literal `❯`).
            # Consume the line outright so it produces no segment at all.
            if _SLASH_COMMAND_RE.fullmatch(prompt.group(1).strip()):
                i += 1
                continue
            body = [prompt.group(1)]
            i += 1
            while i < len(lines):
                cur = lines[i]
                if _cc_starts_block(lines, i):
                    break
                if _cc_result_tip(cur):
                    break  # hand the Tip footer + its wrap to the Tip branch
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
            emit({"type": "user", "text": _reflow_user(body)}, block_start, i)
            continue

        done = _CC_AGENT_DONE_RE.match(line)
        if done:
            emit(
                {
                    "type": "agent_event",
                    "name": done.group(1),
                    "status": "completed",
                    "elapsed": done.group(2),
                },
                block_start,
                i + 1,
            )
            i += 1
            continue

        start = _CC_AGENT_START_RE.match(line)
        if start:
            i += 1
            while i < len(lines) and not _cc_starts_block(lines, i):
                i += 1
            emit(
                {
                    "type": "agent_event",
                    "name": start.group(1),
                    "status": "dispatched",
                    "elapsed": None,
                },
                block_start,
                i,
            )
            continue

        completion = _CC_COMPLETION_RE.match(line)
        if completion:
            attribute_completion(
                spanned, completion.group(1), base + i + 1, on_drop=record_dropped_completion
            )
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
            emit(
                {
                    "type": "tool_call",
                    "title": truncate_title(command),
                    "input": truncate_title(command) if verb == "Bash" else None,
                    "result": result,
                    "elided": elided or result is None,
                    "running": False,
                },
                block_start,
                i,
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
            emit(
                {
                    "type": "tool_call",
                    "title": truncate_title(title),
                    "input": None,
                    "result": result,
                    "elided": elided or result is None,
                    "running": False,
                },
                block_start,
                i,
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
                if _cc_result_tip(lines[i]):
                    break  # hand the Tip footer + its wrap to the Tip branch
                if not _cc_is_chrome(lines[i]):
                    body.append(lines[i])
                elif not lines[i].strip():
                    body.append("")
                i += 1
            text = _reflow_prose(body)
            if text:
                emit(
                    {
                        "type": "assistant",
                        "phase": "intermediate",
                        "text": text,
                        "elapsed": None,
                    },
                    block_start,
                    i,
                )
            continue

        if _cc_result_tip(line):
            # CC's bottom-of-pane ``Tip:`` footer. It soft-wraps, and the live
            # footer rendered under the working spinner wraps its tail back to
            # column 0 (e.g. a lone ``/config`` from "…enable push notifications
            # in /config"). That column-0 tail would otherwise fall into the
            # bare-prose branch below as a phantom assistant turn. Consume the
            # Tip line plus its wrapped continuation as one chrome unit. Stop at
            # the first blank line, real block start, input box / ``⎿`` row, or a
            # *different* chrome construct — so we only ever eat the footer's own
            # wrap, never the content that follows it.
            i += 1
            while i < len(lines):
                cur = lines[i]
                if not cur.strip():
                    break
                if _cc_starts_block(lines, i):
                    break
                if _CC_PROMPT_RE.match(cur) or _CC_RESULT_RE.match(cur):
                    break
                if _cc_is_chrome(cur) and not _cc_result_tip(cur):
                    break
                i += 1
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
                if _cc_result_tip(cur):
                    break  # hand the Tip footer + its wrap to the Tip branch
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
                emit(
                    {
                        "type": "assistant",
                        "phase": "intermediate",
                        "text": text,
                        "elapsed": None,
                    },
                    block_start,
                    i,
                )
            continue

        i += 1
    return dedupe_adjacent_spanned(spanned)


def choice_prompt_segment(prompt: Any) -> ChoicePromptSegment:
    """Convert a MultipleChoicePrompt into a ChoicePromptSegment dict."""
    options: list[ChoiceOptionDict] = [
        {
            "number": option.number,
            "label": option.label,
            "description": option.description or None,
            "checked": option.checked,
        }
        for option in prompt.options
    ]
    return {
        "type": "choice_prompt",
        "question": prompt.question,
        "options": options,
        "footer": prompt.footer or None,
        "selected": (
            None
            if getattr(prompt, "submit_selected", False)
            else prompt.selected_option.number
        ),
        "answered": False,
        "chosen": None,
        "multi": bool(getattr(prompt, "multi_select", False)),
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
