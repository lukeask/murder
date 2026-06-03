"""Transcript parsing seam — separate from harness lifecycle adapters.

Harness adapters expose pane text; parsers here turn captures into
``(role, text)`` turns for :mod:`murder.persistence.conversation`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from murder.harnesses.parsing import (
    is_rule_line,
    is_status_spinner_line,
    is_tool_glyph_line,
    parse_prompt_marker_transcript,
    strip_ansi,
)

TranscriptTurn = tuple[str, str]
"""``(role, text)`` with ``role`` in ``{"user", "assistant"}``."""

# Completed turns in marker-less scrollback are separated by this many
# consecutive blank/chrome lines; a single blank stays an intra-turn break.
_TURN_SEPARATOR_BLANKS = 2


class TranscriptParser(Protocol):
    def parse(self, pane_text: str) -> list[TranscriptTurn]: ...


@dataclass(frozen=True, slots=True)
class PromptMarkerTranscriptParser:
    """Generic prompt-marker heuristic (claude_code, codex, pi, …)."""

    prompt_markers: tuple[str, ...]
    drop_substrings: tuple[str, ...] = ()

    def parse(self, pane_text: str) -> list[TranscriptTurn]:
        return parse_prompt_marker_transcript(
            pane_text,
            prompt_markers=self.prompt_markers,
            drop_substrings=self.drop_substrings,
        )


@dataclass(frozen=True, slots=True)
class PreprocessedPromptMarkerParser:
    """Prompt-marker parser with a pane normalizer applied first."""

    prompt_markers: tuple[str, ...]
    drop_substrings: tuple[str, ...]
    normalize: Callable[[str], str]

    def parse(self, pane_text: str) -> list[TranscriptTurn]:
        normalized = self.normalize(pane_text)
        return parse_prompt_marker_transcript(
            normalized,
            prompt_markers=self.prompt_markers,
            drop_substrings=self.drop_substrings,
        )


# Any line beginning with "→ " is the live input cursor or current-context arrow.
_CURSOR_PLACEHOLDER_RE = re.compile(r"^\s*→\s+", re.IGNORECASE)
_CURSOR_COMPOSER_RE = re.compile(r"^\s*Composer\b.*\bAuto-run\b", re.IGNORECASE)
_CURSOR_USAGE_RE = re.compile(r"^\s*Auto\s+·\s+\d+(?:\.\d+)?%\s*$", re.IGNORECASE)
# The Cursor cwd banner appears as a lone path in the footer.  Tilde-led paths
# (~/…) are always banners; bare absolute paths (/…) are chrome only when they
# carry a "· branch" suffix — without it they are indistinguishable from file
# paths emitted by tool output (e.g. `find` results like /home/…/.gitignore).
_CURSOR_CWD_RE = re.compile(
    r"^\s*(?:"
    r"~/[\w/.-]*"              # tilde-led path (always a CWD banner)
    r"|/[\w/.-]*\s+·\s+\S+"   # absolute path only with "· branch" suffix
    r")\s*$"
)
_BUSY_INPUT_HINT_RE = re.compile(r"ctrl\+c to stop", re.IGNORECASE)
_BUSY_SPINNER_RE = re.compile(
    r"^\s*\S+\s+(Composing|Running|Generating|Thinking)\b",
    re.MULTILINE,
)
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
        # Cursor banner tip line: "Use /slash-cmd to …" or "Use subagents to …"
        |Use\s+\S+\s+to\b
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _is_cursor_chrome(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if is_rule_line(line) or is_status_spinner_line(line):
        return True
    return bool(
        _CURSOR_PLACEHOLDER_RE.match(s)
        or _CURSOR_COMPOSER_RE.match(s)
        or _CURSOR_USAGE_RE.match(s)
        or _CURSOR_CWD_RE.match(s)
        or _BUSY_INPUT_HINT_RE.search(s)
        or _BUSY_SPINNER_RE.match(s)
        or _CURSOR_CHROME_RE.match(s)
    )



@dataclass(frozen=True, slots=True)
class ClaudeCodeTranscriptParser:
    """CC 2.x-specific parser.

    CC echoes submitted text with ``❯ `` on the first line and 2-space-indented
    continuation lines for any subsequent wrapped or multi-paragraph content.
    The first tool-glyph-prefixed line (``⏺`` / ``●`` / …) marks the start of
    the assistant block — everything between the prompt marker and that first
    glyph is user text, not assistant prose.

    Only ``❯`` (U+276F) is used as the prompt marker. The plain ``>`` is
    intentionally excluded: it appears inside git diffs, markdown blockquotes,
    and shell output that CC renders as part of its responses, and treating it
    as a turn boundary would split the assistant's reply into spurious user
    turns.
    """

    prompt_markers: tuple[str, ...] = ("❯",)
    drop_substrings: tuple[str, ...] = ()

    def parse(self, pane_text: str) -> list[TranscriptTurn]:
        return parse_prompt_marker_transcript(
            pane_text,
            prompt_markers=self.prompt_markers,
            drop_substrings=self.drop_substrings,
            user_continuation=lambda line: line.startswith("  "),
            assistant_start=is_tool_glyph_line,
        )


@dataclass(frozen=True, slots=True)
class BlankSeparatedTranscriptParser:
    """Alternating user/assistant turns separated by 2+ consecutive blank lines.

    For harnesses (Pi, etc.) whose scrollback doesn't echo a prompt marker on
    completed turns.  After chrome filtering via ``is_chrome_fn`` (or the
    rule/spinner/drop-substring fallback), content paragraphs separated by two
    or more consecutive blank lines alternate user → assistant → user → …

    A single blank line within a block is preserved as an intra-turn paragraph
    break, which lets Pi's model-thinking prefix remain attached to its reply
    block rather than starting a spurious new turn.

    ``chrome_resets_blank_counter`` changes how chrome lines are counted.
    When False (default), chrome lines increment the blank counter just like
    real blank lines — two consecutive chrome/blank lines trigger a turn split.
    When True, chrome lines *reset* the counter to zero instead of incrementing
    it; only consecutive real blank lines count toward the split threshold.
    Use True for Cursor, where ``────`` rule lines appear *inside* tool output
    blocks (separated from surrounding content by single blank lines), and
    treating them as blank equivalents would split a single assistant turn
    at every file-content separator.

    Cursor reuses this parser with its own ``is_chrome_fn`` and
    ``chrome_resets_blank_counter=True``; there is no Cursor-specific class.
    """

    is_chrome_fn: Callable[[str], bool] | None = None
    drop_substrings: tuple[str, ...] = ()
    normalize_body: Callable[[str, str], str] | None = None
    chrome_resets_blank_counter: bool = False

    def parse(self, pane_text: str) -> list[TranscriptTurn]:
        lines = strip_ansi(pane_text).splitlines()
        lowered_drops = tuple(d.lower() for d in self.drop_substrings)

        def is_chrome(line: str) -> bool:
            if self.is_chrome_fn is not None:
                return bool(self.is_chrome_fn(line))
            s = line.strip()
            if not s:
                return False
            if is_rule_line(line) or is_status_spinner_line(line):
                return True
            return any(d in s.lower() for d in lowered_drops)

        turn_blocks: list[list[str]] = []
        cur: list[str] = []
        consec_blank = 0

        for line in lines:
            is_blank = not line.strip()
            if is_blank or is_chrome(line):
                if is_blank:
                    consec_blank += 1
                elif self.chrome_resets_blank_counter:
                    consec_blank = 0
                else:
                    consec_blank += 1
                if consec_blank == _TURN_SEPARATOR_BLANKS and cur:
                    turn_blocks.append(cur)
                    cur = []
            else:
                if consec_blank == 1 and cur:
                    cur.append("")
                consec_blank = 0
                cur.append(line.strip())

        if cur:
            turn_blocks.append(cur)

        bodies: list[str] = []
        for block in turn_blocks:
            while block and not block[-1]:
                block.pop()
            while block and not block[0]:
                block.pop(0)
            text_body = "\n".join(block).strip()
            if text_body:
                bodies.append(text_body)

        # Anchor parity on the BOTTOM, not the top: at idle the last completed
        # block is the assistant's reply (the live input box / spinner below it
        # is chrome-filtered). Counting roles back from the end survives the
        # common case where the top of the captured scrollback is a clipped,
        # scrolled-past assistant turn — a forward "block 0 is the user" parity
        # would flip every role the moment that happens.
        n = len(bodies)
        turns: list[TranscriptTurn] = []
        for i, body in enumerate(bodies):
            role = "assistant" if (n - 1 - i) % 2 == 0 else "user"
            if self.normalize_body is not None:
                body = self.normalize_body(role, body).strip()
            if not body:
                continue
            turns.append((role, body))

        return turns


_BUILTIN_PARSERS: dict[str, TranscriptParser] = {
    # Cursor uses double blank lines between turns and single blanks within
    # turns.  chrome_resets_blank_counter=True prevents rule lines (────) that
    # appear inside Cursor's tool-output display from being counted as blank
    # equivalents and creating false turn splits.
    "cursor": BlankSeparatedTranscriptParser(
        is_chrome_fn=_is_cursor_chrome,
        chrome_resets_blank_counter=True,
    ),
}


def register_parser(kind: str, parser: TranscriptParser) -> None:
    """Register a harness-specific parser (e.g. pi chrome stripping)."""
    _BUILTIN_PARSERS[kind] = parser


def parser_for_harness_kind(
    kind: str,
    *,
    prompt_markers: tuple[str, ...] = (),
    drop_substrings: tuple[str, ...] = (),
) -> TranscriptParser | None:
    """Resolve a parser for a harness kind and marker configuration."""
    if kind == "claude_code":
        # Read the adapter's marker ClassVar so there is a single source of
        # truth; fall back to CC's known markers when none were supplied.
        return ClaudeCodeTranscriptParser(
            prompt_markers=prompt_markers or ("❯",),
            drop_substrings=drop_substrings,
        )
    if kind in _BUILTIN_PARSERS:
        return _BUILTIN_PARSERS[kind]
    if not prompt_markers:
        return None
    return PromptMarkerTranscriptParser(
        prompt_markers=prompt_markers,
        drop_substrings=drop_substrings,
    )


def has_transcript_parser(
    kind: str,
    *,
    prompt_markers: tuple[str, ...] = (),
) -> bool:
    return parser_for_harness_kind(kind, prompt_markers=prompt_markers) is not None


def parse_transcript_for_adapter(adapter: object, pane_text: str) -> list[TranscriptTurn]:
    """Parse using the adapter's kind and transcript class vars."""
    cls = type(adapter)
    parser = parser_for_harness_kind(
        getattr(cls, "kind", ""),
        prompt_markers=getattr(cls, "transcript_prompt_markers", ()),
        drop_substrings=getattr(cls, "transcript_drop_substrings", ()),
    )
    if parser is None:
        return []
    return parser.parse(pane_text)


__all__ = [
    "BlankSeparatedTranscriptParser",
    "ClaudeCodeTranscriptParser",
    "PromptMarkerTranscriptParser",
    "PreprocessedPromptMarkerParser",
    "TranscriptParser",
    "TranscriptTurn",
    "has_transcript_parser",
    "parse_transcript_for_adapter",
    "parser_for_harness_kind",
    "register_parser",
]
