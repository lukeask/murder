"""Multiple-choice prompt seam shared by transcript parsing and the TUI."""

from __future__ import annotations

import re
from dataclasses import dataclass

_OPTION_RE = re.compile(r"^[ \t]*(?:(❯|>)[ \t\xa0]+)?(\d+)\.\s+(.*)$")
_SEPARATOR_RE = re.compile(r"^[─\-]{10,}$")
_FOOTER_RE = re.compile(r"Enter to (confirm|select)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ChoiceOption:
    number: int
    label: str
    description: str = ""


@dataclass(frozen=True, slots=True)
class MultipleChoicePrompt:
    question: str
    options: tuple[ChoiceOption, ...]
    selected_index: int = 0
    footer: str = ""

    @property
    def selected_option(self) -> ChoiceOption:
        return self.options[self.selected_index]


def parse_claude_code_choice_prompt(pane_text: str) -> MultipleChoicePrompt | None:
    """Return a live CC multiple-choice prompt if the pane currently shows one."""

    lines = pane_text.splitlines()
    options_raw: list[tuple[int, bool, int, str]] = []
    for i, line in enumerate(lines):
        match = _OPTION_RE.match(line)
        if match is None:
            continue
        cursor_char, num_str, label = match.group(1), match.group(2), match.group(3)
        options_raw.append((i, cursor_char is not None, int(num_str), label.strip()))

    if len(options_raw) < 2:
        return None
    if not any(has_cursor for _, has_cursor, _, _ in options_raw):
        return None

    option_descs: dict[int, str] = {}
    for idx, (lineno, _has_cursor, number, _label) in enumerate(options_raw):
        desc_lines: list[str] = []
        next_option_line = options_raw[idx + 1][0] if idx + 1 < len(options_raw) else len(lines)
        for j in range(lineno + 1, next_option_line):
            candidate = lines[j]
            if _SEPARATOR_RE.match(candidate.strip()):
                break
            stripped = candidate.strip()
            if _FOOTER_RE.search(stripped):
                break
            if stripped and not _OPTION_RE.match(candidate):
                desc_lines.append(stripped)
            elif not stripped and desc_lines:
                break
        if desc_lines:
            option_descs[number] = " ".join(desc_lines)

    selected_index = 0
    for idx, (_lineno, has_cursor, _number, _label) in enumerate(options_raw):
        if has_cursor:
            selected_index = idx
            break

    first_option_lineno = options_raw[0][0]
    question = ""
    for j in range(first_option_lineno - 1, -1, -1):
        candidate = lines[j].strip()
        if candidate and not _SEPARATOR_RE.match(candidate):
            question = candidate
            break

    footer = ""
    last_option_lineno = options_raw[-1][0]
    for j in range(last_option_lineno + 1, min(last_option_lineno + 6, len(lines))):
        candidate = lines[j].strip()
        if _FOOTER_RE.search(candidate):
            footer = candidate
            break

    options = tuple(
        ChoiceOption(number=num, label=label, description=option_descs.get(num, ""))
        for _lineno, _has_cursor, num, label in options_raw
    )
    return MultipleChoicePrompt(
        question=question,
        options=options,
        selected_index=selected_index,
        footer=footer,
    )


__all__ = [
    "ChoiceOption",
    "MultipleChoicePrompt",
    "parse_claude_code_choice_prompt",
]
