"""Cursor harness grammar plugin."""

from __future__ import annotations

import logging
import re

from murder.llm.harnesses.parsing import is_rule_line, is_status_spinner_line, strip_ansi
from murder.llm.harnesses.transcripts._shared import (
    dedupe_adjacent_spanned,
    murder_owned_anchors,
    normalize_prompt_match,
    truncate_title,
)
from murder.llm.harnesses.transcripts.toolkit import collect_chrome_delimited_blocks
from murder.llm.harnesses.transcripts.segments import Segment, SpannedSegment

# Cursor colour-codes every submitted *user-input* block with one SGR
# background and the live composer (input box) with another, so both turn role
# and live-UI chrome are recoverable from colour. We capture with escapes (-e)
# and tag the matching lines with sentinels that survive ANSI stripping and
# scrollback reconciliation, then classify by the tags instead of by fragile
# positional alternation. See preprocess_frame / WANTS_ANSI.
WANTS_ANSI = True
_USER_BG_RE = re.compile(r"\x1b\[48;2;36;36;40m")  # submitted user-input block
_INPUT_BG_RE = re.compile(r"\x1b\[48;2;21;21;21m")  # live composer / input box
_USER_MARK = "\x01"
_CHROME_MARK = "\x02"

_log = logging.getLogger(__name__)

# Fires once (process-wide) the first time preprocess_frame sees a frame with
# real content but zero colour marks — the signal that ``-e`` colour capture is
# missing or Cursor changed its background RGBs. Role detection degrades to the
# anchor-only fallback in that case, so we want it loud but not flooding.
_warned_no_marks = False

# ---- cursor regexes -------------------------------------------------------- #
_CURSOR_INPUT_LINE_RE = re.compile(r"^\s*→\s*\S")
_CURSOR_STARTUP_HINT_RE = re.compile(r"^(?:Use\s+/\S|Try\s+Composer\b)", re.IGNORECASE)

# The cursor *chrome* predicate and the regexes it owns live here (the grammar
# owns them); the cursor adapter imports ``_is_cursor_chrome`` back, plus the few
# regexes that double as live-state markers. Layering: adapter→grammar is allowed,
# grammar→adapter is not.
_BUSY_INPUT_HINT_RE = re.compile(r"ctrl\+c to stop", re.IGNORECASE)
_BUSY_SPINNER_RE = re.compile(
    r"^\s*\S+\s+(Composing|Running|Generating|Thinking)\b",
    re.MULTILINE,
)
_CURSOR_CWD_RE = re.compile(r"^\s*(?:~/|/|\./|\.\./).*\s+·\s+\S+\s*$")
# Some Cursor builds (and narrower terminals) render the cwd banner with the
# separator dot *leading* the path — ``· ~/Documents/code/project`` — instead of
# ``~/path · branch``. That shape starts with ``·``, not ``~/``, so it slips past
# _CURSOR_CWD_RE and, being indented, also past the bare-line fallback, leaking
# the banner into the transcript (and duplicating around the first user turn,
# since the banner repaints above and below it). Match the dot-first form too,
# kept tight to a ``~/``- or ``/``-rooted path so a user's prose starting with
# ``·`` is never swallowed.
_CURSOR_CWD_LEADING_DOT_RE = re.compile(r"^\s*·\s+(?:~/|/)\S")
# Status line: model label + the auto-run mode on the right. CLI ≥ 2026.06.11
# renders the yolo mode as "Run Everything" (older builds said "Auto-run").
_CURSOR_COMPOSER_RE = re.compile(
    r"^\s*Composer\b.*\b(?:Auto-run|Run\s+Everything)\b", re.IGNORECASE
)
_CURSOR_PLACEHOLDER_RE = re.compile(
    r"^\s*→\s*(?:Add a follow-up|Plan,\s*search,\s*build anything)\b",
    re.IGNORECASE,
)
# Cursor's ``/`` command palette / autocomplete dropdown, e.g.
#   · /create-rule       Create Cursor rules for persistent AI guidance. …
#   /babysit             Keep a PR merge-ready by triaging comments, …
# It renders as an overlay above the input and repaints as you type, so it both
# leaks into the scrollback and *duplicates* (the same /command row appearing 2-3
# times in a row). Each row is a ``/command`` token followed by a column-aligned
# gap (2+ spaces) then a description. The 2+-space gap after a ``/command`` at a
# word boundary is the distinctive layout signal — required so file paths like
# ``src/main`` (no word boundary before ``/``) and inline command mentions like
# ``/help to reset`` (single space) are never swallowed. Matched anywhere on the
# line so the wrapped/scrolled ``clear… /create-rule  …`` repaint form is caught.
_CURSOR_SLASH_PALETTE_RE = re.compile(r"(?:^|[\s·•▸▶❯›>])/[a-z][a-z0-9-]*\s{2,}\S")
_CURSOR_CHROME_RE = re.compile(
    r"""
    ^\s*(?:
        Cursor\s+Agent
        |v\d{4}\.\d{2}\.\d{2}-[A-Za-z0-9]+
        |⚠\s*Workspace\s+Trust\s+Required
        |Cursor\s+Agent\s+can\s+execute\s+code\b
        |Do\s+you\s+trust\s+the\s+contents\b
        |\[[aq]\]\s+
        |⏳\s*Trusting\s+workspace
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _is_cursor_chrome(line: str) -> bool:
    """True if a pane line is cursor UI chrome (busy markers, cwd, banner, prompt
    placeholder). Doubles as the busy/composer marker source for the adapter."""
    s = line.strip()
    if not s:
        return False
    if is_rule_line(line) or is_status_spinner_line(line):
        return True
    return bool(
        _CURSOR_PLACEHOLDER_RE.match(s)
        or _CURSOR_COMPOSER_RE.match(s)
        or _CURSOR_CWD_RE.match(s)
        or _CURSOR_CWD_LEADING_DOT_RE.match(s)
        or _BUSY_INPUT_HINT_RE.search(s)
        or _BUSY_SPINNER_RE.match(s)
        or _CURSOR_CHROME_RE.match(s)
    )


def preprocess_frame(frame: str) -> str:
    """Tag user-input lines, then strip ANSI for the rest of the pipeline.

    Run on the raw ``-e`` capture before scrollback reconciliation. Lines Cursor
    painted with the user-input background get a ``_USER_MARK`` prefix; lines in
    the live composer get a ``_CHROME_MARK`` prefix (its wrapped continuations
    carry no ``→`` marker, so colour is the only reliable way to suppress them).
    The marks are preserved verbatim by ``strip_ansi`` and stable across frames,
    so they travel with the line through scrollback. Frames captured without
    escapes carry no marks and fall through to the anchor-based classifier.
    """
    global _warned_no_marks
    out: list[str] = []
    marks = 0
    has_content = False
    for raw in frame.splitlines():
        plain = strip_ansi(raw)
        if plain.strip():
            has_content = True
        if _INPUT_BG_RE.search(raw):
            out.append(_CHROME_MARK + plain)
            marks += 1
        elif _USER_BG_RE.search(raw):
            out.append(_USER_MARK + plain)
            marks += 1
        else:
            out.append(plain)
    if has_content and marks == 0 and not _warned_no_marks:
        _warned_no_marks = True
        _log.warning(
            "cursor: no ANSI colour marks found in frame — role detection "
            "degraded (check tmux -e flag)"
        )
    return "\n".join(out)


def _classify(line: str) -> tuple[bool, bool, str]:
    """Split a possibly-tagged line into ``(is_user_input, is_chrome, plain)``."""
    if line.startswith(_USER_MARK):
        return True, False, line[len(_USER_MARK):]
    if line.startswith(_CHROME_MARK):
        return False, True, line[len(_CHROME_MARK):]
    return False, False, line


def _cursor_is_chrome(line: str) -> bool:
    """Chrome predicate for cursor transcript parsing.

    Extends `_is_cursor_chrome` with patterns that are not needed for idle/busy
    state detection but must be suppressed in the parsed transcript.
    """
    _is_user, is_chrome, line = _classify(line)
    if is_chrome:
        return True
    s = line.strip()
    return bool(
        not s
        or _is_cursor_chrome(line)
        or _CURSOR_SLASH_PALETTE_RE.search(line)
        or _CURSOR_INPUT_LINE_RE.match(line)
        or s.startswith("Tip:")
        or _CURSOR_STARTUP_HINT_RE.match(s)
        or not line.startswith(" ")
    )


# ---- tool-activity rollups ------------------------------------------------- #
# Cursor has no tool-call glyphs (unlike CC's ⏺/⎿); it paints tool activity as
# rollup lines that progressively redraw as actions accumulate, e.g.
#     Grepping, searching 1 grep, 1 search Grepped "pat" in . …      (in-progress)
#     Grepped, searched 1 grep, 1 search Grepped "pat" in . …        (done)
#     Editing allctrlbinds.md   →   Edited allctrlbinds.md   +26
# We recover these as ``tool_call`` segments and collapse each consecutive redraw
# run to its final, most-complete frame. The prose trap — "Searching the codebase
# for where reset times are formatted" is narration, not a tool call — is handled
# by requiring a hard tool *signal* (counts / path / quoted pattern / diffstat /
# "Found N matches" / "… N earlier items hidden" / "lines X-Y") and rejecting an
# article right after the verb.
_ROLLUP_VERB = (
    r"(?:Grepp(?:ing|ed)|Search(?:ing|ed)|Read(?:ing)?|Edit(?:ing|ed)|"
    r"List(?:ing|ed)|Glob(?:bing|bed)|Call(?:ing|ed)|Runn(?:ing)?|Ran|"
    r"Creat(?:ing|ed)|Delet(?:ing|ed)|Writ(?:ing)|Wrote|Fetch(?:ing|ed)|"
    r"Mov(?:ing|ed)|Renam(?:ing|ed)|Append(?:ing|ed)|Remov(?:ing|ed))"
)
_ROLLUP_LEAD_RE = re.compile(rf"^\s*{_ROLLUP_VERB}(?:,\s*\w+)*\b", re.IGNORECASE)
_ROLLUP_ARTICLE_RE = re.compile(
    rf"^\s*{_ROLLUP_VERB}\s+(?:the|a|an|for|to|through|into|over|about|all|this|that|each|its|our|my)\b",
    re.IGNORECASE,
)
_ROLLUP_GERUND_RE = re.compile(rf"^\s*{_ROLLUP_VERB}", re.IGNORECASE)
_ROLLUP_SHELL_RE = re.compile(r"^\s*\$\s+\S")
_ROLLUP_SIGNALS = (
    re.compile(r"\b\d+\s+(?:grep|search|file|glob|match|line|edit|read|tool|command|terminal)s?\b", re.I),
    re.compile(r"\bFound\s+\d+\s+(?:match|file)", re.I),
    re.compile(r"…\s*\d+\s+earlier items hidden", re.I),
    re.compile(r'"[^"]*"\s+in\s+\S'),
    re.compile(r"\blines?\s+\d+-\d+\b", re.I),
    re.compile(r"(?:^|\s)[+-]\d+(?:\s+[+-]\d+)?\s*$"),
    re.compile(r"\b\S+/\S+\b"),
    re.compile(r"\b[\w.-]+\.(?:ts|tsx|js|jsx|py|md|json|yaml|yml|txt|sh|toml|cfg|rs|go)\b"),
)


def _is_cursor_tool_rollup(text: str) -> bool:
    """True if a chrome-stripped assistant block is a Cursor tool-activity rollup."""
    s = text.strip()
    if not s:
        return False
    if _ROLLUP_SHELL_RE.match(s):
        return True
    if not _ROLLUP_LEAD_RE.match(s) or _ROLLUP_ARTICLE_RE.match(s):
        return False
    return any(sig.search(s) for sig in _ROLLUP_SIGNALS)


def _rollup_is_running(text: str) -> bool:
    """True for the in-progress (gerund) redraw frame, e.g. 'Grepping …'."""
    m = _ROLLUP_GERUND_RE.match(text.strip())
    return bool(m and m.group(0).rstrip().lower().endswith("ing"))


def _rollup_title(text: str) -> str:
    """Compact title for a tool rollup.

    Shell -> the command. Count-summary ("Grepped, read 3 files, 1 grep Read …")
    -> the count head, dropping the expanded per-action detail. Single action
    ("Reading inktui/src/foo.ts") -> the whole short line (the path is the point).
    """
    s = " ".join(text.split())
    if _ROLLUP_SHELL_RE.match(s):
        return truncate_title(s.lstrip("$ ").strip())
    if _ROLLUP_COUNT_RE.search(s):  # trim expanded detail after the count summary
        cut = re.search(r'\s(?="|[A-Za-z.]+/|…)', s)
        if cut:
            s = s[: cut.start()].strip() or s
    return truncate_title(s)


def _tool_call_segment(text: str) -> Segment:
    return {
        "type": "tool_call",
        "title": _rollup_title(text),
        "input": None,
        "result": text,
        "elided": "earlier items hidden" in text,
        "running": _rollup_is_running(text),
    }


_ROLLUP_COUNT_RE = re.compile(r"(\d+)\s+(?:grep|search|file|glob|match|line|edit|read)s?\b", re.I)
_ROLLUP_PATH_RE = re.compile(r"(\S+/\S+|[\w.-]+\.\w{1,5})")


def _rollup_signature(text: str) -> tuple[str, object]:
    """Classify a rollup for redraw-chain continuation.

    A *redraw chain* is the SAME accumulating operation repainted (its counts only
    grow); distinct operations — a shell command, a different file edit — must not
    merge. Returns ``(kind, key)``:
      shell  -> each command is its own op, never continues.
      count  -> count-summary rollup; key = total count (monotonic across a chain).
      single -> single-action rollup; key = its first path/filename token.
    """
    s = text.strip()
    if _ROLLUP_SHELL_RE.match(s):
        return ("shell", s)
    counts = _ROLLUP_COUNT_RE.findall(s)
    if counts:
        return ("count", sum(int(c) for c in counts))
    m = _ROLLUP_PATH_RE.search(s)
    return ("single", m.group(1) if m else s)


def _rollup_continues(prev: str, cur: str) -> bool:
    """True if ``cur`` is a later redraw frame of the same operation as ``prev``."""
    pk, pv = _rollup_signature(prev)
    ck, cv = _rollup_signature(cur)
    if pk == "count" and ck == "count":
        return cv >= pv  # same accumulating group; counts only grow
    if pk == "single" and ck == "single":
        return pv == cv  # "Editing X" -> "Edited X" (same target)
    return False


def _collapse_tool_rollups(spanned: list[SpannedSegment]) -> list[SpannedSegment]:
    """Collapse each redraw chain of tool_call rollups to its final frame.

    Cursor repaints a rollup as actions accumulate, so one operation shows up as
    several adjacent tool_call frames; keep only the last (most complete), but
    only merge frames that continue the *same* operation (see _rollup_continues)
    so distinct tool calls stay separate.
    """
    out: list[SpannedSegment] = []
    i = 0
    while i < len(spanned):
        if spanned[i].segment.get("type") != "tool_call":
            out.append(spanned[i])
            i += 1
            continue
        j = i
        while (
            j + 1 < len(spanned)
            and spanned[j + 1].segment.get("type") == "tool_call"
            and _rollup_continues(spanned[j].segment["result"], spanned[j + 1].segment["result"])
        ):
            j += 1
        last = spanned[j]
        out.append(SpannedSegment(last.segment, spanned[i].start, last.end))
        i = j + 1
    return out


def parse_lines(
    lines: list[str],
    system_prompt: str | None = None,
    user_texts: list[str] | None = None,
) -> list[Segment]:
    return [s.segment for s in parse_spanned(lines, system_prompt, user_texts)]


def parse_spanned(
    lines: list[str],
    system_prompt: str | None = None,
    user_texts: list[str] | None = None,
) -> list[SpannedSegment]:
    """Parse cursor scrollback into span-annotated segments.

    Cursor has no *syntactic* role markers, but it colour-codes user-input
    blocks; ``preprocess_frame`` tags those lines with ``_USER_MARK``. We split
    the pane into blank-line blocks and classify each as ``user`` (a tagged
    block, or one whose text reconstructs murder's own system prompt / a
    ground-truth user turn) versus ``assistant`` (everything else). The
    positional alternation this replaces scrambled roles whenever a turn spanned
    multiple paragraphs or Cursor re-rendered the injected prompt mid-pane.

    Parsed ``user`` segments are dropped downstream in favour of authoritative
    user blocks, so the only job here is to keep genuine assistant prose and
    discard murder's own echoed content.
    """
    blocks = collect_chrome_delimited_blocks(lines, _cursor_is_chrome)
    anchors = murder_owned_anchors(system_prompt, user_texts)

    spanned: list[SpannedSegment] = []
    for block, start, end in blocks:
        classified = [_classify(line) for line in block]
        is_user = any(is_u for is_u, _is_chrome, _plain in classified)
        text = " ".join(plain.strip() for _is_u, _is_chrome, plain in classified if plain.strip())
        if not text:
            continue
        if is_user or normalize_prompt_match(text) in anchors:
            spanned.append(SpannedSegment({"type": "user", "text": text}, start, end))
        elif _is_cursor_tool_rollup(text):
            spanned.append(SpannedSegment(_tool_call_segment(text), start, end))
        else:
            spanned.append(
                SpannedSegment(
                    {"type": "assistant", "phase": "intermediate", "text": text, "elapsed": None},
                    start,
                    end,
                )
            )
    return _collapse_tool_rollups(dedupe_adjacent_spanned(spanned))


def is_idle(pane_text: str) -> bool:
    """True when the cursor pane is awaiting input."""
    from murder.llm.harnesses.cursor import CursorAdapter  # noqa: PLC0415

    return CursorAdapter().is_idle(pane_text)


def detect_live_choice_prompt(frame: str) -> None:  # type: ignore[return]
    """Cursor has no choice prompt UI."""
    return None


def close_last_turn(segments: list[Segment]) -> None:
    """At idle, all cursor assistant blocks are complete turns — mark all final."""
    for segment in segments:
        if segment["type"] == "assistant" and segment.get("phase") == "intermediate":
            segment["phase"] = "final"
