"""Transcript parsing seam — separate from harness lifecycle adapters.

Harness adapters expose pane text; parsers here turn captures into
``(role, text)`` turns for :mod:`murder.persistence.conversation`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from murder.harnesses.parsing import is_rule_line, is_status_spinner_line, strip_ansi

TranscriptTurn = tuple[str, str]
"""``(role, text)`` with ``role`` in ``{"user", "assistant"}``."""


class TranscriptParser(Protocol):
    def parse(self, pane_text: str) -> list[TranscriptTurn]: ...


@dataclass(frozen=True, slots=True)
class PromptMarkerTranscriptParser:
    """Generic prompt-marker heuristic (claude_code, codex, pi, …)."""

    prompt_markers: tuple[str, ...]
    drop_substrings: tuple[str, ...] = ()

    def parse(self, pane_text: str) -> list[TranscriptTurn]:
        from murder.harnesses.parsing import parse_prompt_marker_transcript

        text = pane_text
        return parse_prompt_marker_transcript(
            text,
            prompt_markers=self.prompt_markers,
            drop_substrings=self.drop_substrings,
        )


@dataclass(frozen=True, slots=True)
class PreprocessedPromptMarkerParser:
    """Prompt-marker parser with a pane normalizer applied first."""

    prompt_markers: tuple[str, ...]
    drop_substrings: tuple[str, ...]
    normalize: object  # Callable[[str], str] — avoid import cycle typing

    def parse(self, pane_text: str) -> list[TranscriptTurn]:
        from murder.harnesses.parsing import parse_prompt_marker_transcript

        normalized = self.normalize(pane_text)  # type: ignore[operator]
        return parse_prompt_marker_transcript(
            normalized,
            prompt_markers=self.prompt_markers,
            drop_substrings=self.drop_substrings,
        )


_CURSOR_USER_LINE_MIN_WIDTH = 72
_CURSOR_USER_LINE_MIN_TRAILING_SPACES = 4

_CURSOR_PLACEHOLDER_RE = re.compile(
    r"^\s*→\s*(?:Add a follow-up|Plan,\s*search,\s*build anything)\b",
    re.IGNORECASE,
)
_CURSOR_COMPOSER_RE = re.compile(r"^\s*Composer\b.*\bAuto-run\b", re.IGNORECASE)
_CURSOR_CWD_RE = re.compile(r"^\s*(?:~/|/|\./|\.\./).*\s+·\s+\S+\s*$")
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
        or _CURSOR_CWD_RE.match(s)
        or _BUSY_INPUT_HINT_RE.search(s)
        or _BUSY_SPINNER_RE.match(s)
        or _CURSOR_CHROME_RE.match(s)
    )


def _is_cursor_user_line(line: str) -> bool:
    if not line.strip():
        return False
    return (
        len(line) >= _CURSOR_USER_LINE_MIN_WIDTH
        and len(line) - len(line.rstrip()) >= _CURSOR_USER_LINE_MIN_TRAILING_SPACES
    )


def _join_cursor_user_lines(lines: list[str]) -> str:
    return " ".join(line.strip() for line in lines if line.strip()).strip()


def _clean_cursor_assistant_line(line: str) -> str:
    return line[2:] if line.startswith("  ") else line.rstrip()


@dataclass(frozen=True, slots=True)
class CursorTranscriptParser:
    """Cursor agent CLI pane layout (full-width user blocks)."""

    def parse(self, pane_text: str) -> list[TranscriptTurn]:
        lines = strip_ansi(pane_text).splitlines()
        turns: list[TranscriptTurn] = []
        current_user: str | None = None
        user_lines: list[str] = []
        assistant_lines: list[str] = []
        in_user_block = False

        def flush_assistant() -> None:
            nonlocal assistant_lines
            if current_user is None:
                assistant_lines = []
                return
            body = "\n".join(line.rstrip() for line in assistant_lines).strip()
            if body:
                turns.append(("user", current_user))
                turns.append(("assistant", body))
            assistant_lines = []

        def flush_user_block() -> None:
            nonlocal current_user, user_lines, in_user_block
            if not in_user_block:
                return
            prompt = _join_cursor_user_lines(user_lines)
            if prompt:
                flush_assistant()
                current_user = prompt
            user_lines = []
            in_user_block = False

        for line in lines:
            if _is_cursor_chrome(line):
                continue
            if _is_cursor_user_line(line):
                if not in_user_block:
                    in_user_block = True
                    user_lines = []
                user_lines.append(line)
                continue
            flush_user_block()
            if current_user is None:
                continue
            assistant_lines.append(_clean_cursor_assistant_line(line))

        flush_user_block()
        flush_assistant()
        return turns


_BUILTIN_PARSERS: dict[str, TranscriptParser] = {
    "cursor": CursorTranscriptParser(),
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
    "CursorTranscriptParser",
    "PromptMarkerTranscriptParser",
    "PreprocessedPromptMarkerParser",
    "TranscriptParser",
    "TranscriptTurn",
    "has_transcript_parser",
    "parse_transcript_for_adapter",
    "parser_for_harness_kind",
    "register_parser",
]
