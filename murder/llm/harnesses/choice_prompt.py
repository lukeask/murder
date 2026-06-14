"""Multiple-choice prompt seam shared by transcript parsing and the TUI."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Option line: optional ❯ cursor, "N.", then (multi-select only) a "[ ]"/"[✔]"
# checkbox, then the label. The checkbox group distinguishes CC's multiSelect
# AskUserQuestion menus from single-select ones.
_OPTION_RE = re.compile(
    r"^[ \t]*(?:(❯|>)[ \t\xa0]+)?(\d+)\.\s+(?:\[([ ✔✓xX])\][ \t]+)?(.*)$"
)
_SEPARATOR_RE = re.compile(r"^[─\-]{10,}$")
_FOOTER_RE = re.compile(r"Enter to (confirm|select)", re.IGNORECASE)
# Chrome lines inside the multi-select dialog that must not be read as option
# descriptions: the (optionally cursored) "Submit" row and the
# "←  ☐ Tab  ✔ Submit  →" header.
_MULTI_CHROME_RE = re.compile(r"^(?:(?:❯|>)[ \t\xa0]+)?(Submit|←.*→)$")
# The multi-select dialog's dedicated Submit row with the cursor on it. It is
# unnumbered, so no numbered option carries the ❯ while it is selected.
_SUBMIT_CURSOR_RE = re.compile(r"^[ \t]*(?:❯|>)[ \t\xa0]+Submit[ \t]*$")


@dataclass(frozen=True, slots=True)
class ChoiceOption:
    number: int
    label: str
    description: str = ""
    # None on single-select menus; True/False = the checkbox state on a
    # multi-select (CC AskUserQuestion multiSelect) menu.
    checked: bool | None = None


@dataclass(frozen=True, slots=True)
class MultipleChoicePrompt:
    question: str
    options: tuple[ChoiceOption, ...]
    selected_index: int = 0
    footer: str = ""
    # True when the menu is a multi-select (any option rendered a checkbox).
    multi_select: bool = False
    # True when the dialog cursor sits on the multi-select's dedicated
    # (unnumbered) Submit row — selected_index/selected_option are meaningless
    # then and consumers must check this first.
    submit_selected: bool = False

    @property
    def selected_option(self) -> ChoiceOption:
        return self.options[self.selected_index]

    @property
    def checked_numbers(self) -> tuple[int, ...]:
        """The numbers of the currently checked options (multi-select only)."""
        return tuple(o.number for o in self.options if o.checked)


def parse_claude_code_choice_prompt(pane_text: str) -> MultipleChoicePrompt | None:
    """Return a live CC multiple-choice prompt if the pane currently shows one."""

    lines = pane_text.splitlines()
    options_raw: list[tuple[int, bool, int, str, bool | None]] = []
    for i, line in enumerate(lines):
        match = _OPTION_RE.match(line)
        if match is None:
            continue
        cursor_char, num_str, checkbox, label = (
            match.group(1),
            match.group(2),
            match.group(3),
            match.group(4),
        )
        checked: bool | None = None if checkbox is None else checkbox != " "
        options_raw.append((i, cursor_char is not None, int(num_str), label.strip(), checked))

    if not options_raw:
        return None

    # Stray numbered lines elsewhere in the pane — e.g. an instruction list in
    # the scrollback — also match _OPTION_RE. The live menu is the trailing run
    # whose numbers count up sequentially (CC numbers options from 1). Walk back
    # from the last match keeping options while each is exactly one less than the
    # one below it, then drop everything before that run.
    trailing = [options_raw[-1]]
    for entry in reversed(options_raw[:-1]):
        if entry[2] == trailing[-1][2] - 1:
            trailing.append(entry)
        else:
            break
    trailing.reverse()
    options_raw = trailing

    if len(options_raw) < 2:
        return None
    submit_selected = False
    if not any(has_cursor for _, has_cursor, _, _, _ in options_raw):
        # No numbered option carries the cursor — the dialog is still live if
        # the cursor sits on the multi-select's unnumbered Submit row (rendered
        # between the last checkbox option and "Chat about this").
        region_start = options_raw[0][0]
        region_end = min(options_raw[-1][0] + 6, len(lines))
        submit_selected = any(
            _SUBMIT_CURSOR_RE.match(lines[j]) for j in range(region_start, region_end)
        )
        if not submit_selected:
            return None

    option_descs: dict[int, str] = {}
    for idx, (lineno, _has_cursor, number, _label, _checked) in enumerate(options_raw):
        desc_lines: list[str] = []
        next_option_line = options_raw[idx + 1][0] if idx + 1 < len(options_raw) else len(lines)
        for j in range(lineno + 1, next_option_line):
            candidate = lines[j]
            if _SEPARATOR_RE.match(candidate.strip()):
                break
            stripped = candidate.strip()
            if _FOOTER_RE.search(stripped):
                break
            if _MULTI_CHROME_RE.match(stripped):
                break
            if stripped and not _OPTION_RE.match(candidate):
                desc_lines.append(stripped)
            elif not stripped and desc_lines:
                break
        if desc_lines:
            option_descs[number] = " ".join(desc_lines)

    selected_index = 0
    for idx, (_lineno, has_cursor, _number, _label, _checked) in enumerate(options_raw):
        if has_cursor:
            selected_index = idx
            break

    # The question is the contiguous run of text lines directly above the first
    # option. CC wraps a long question across several physical lines when the
    # pane is narrow, so taking only the first line found (the old behaviour)
    # surfaced just the trailing fragment — e.g. "chords). Want a fallback
    # binding too?" instead of the whole sentence. Skip the blank/separator gap
    # above the options, then gather lines until the run is bounded by a blank
    # line, a separator rule, the category/tab header (a "☐ Category" line or the
    # multi-question "← … →" tab bar), or the top of the pane.
    first_option_lineno = options_raw[0][0]
    j = first_option_lineno - 1
    while j >= 0 and (not lines[j].strip() or _SEPARATOR_RE.match(lines[j].strip())):
        j -= 1
    question_parts: list[str] = []
    while j >= 0:
        candidate = lines[j].strip()
        if (
            not candidate
            or _SEPARATOR_RE.match(candidate)
            or _MULTI_CHROME_RE.match(candidate)
            or candidate.startswith("☐")
        ):
            break
        question_parts.append(candidate)
        j -= 1
    question_parts.reverse()
    question = " ".join(question_parts)

    footer = ""
    last_option_lineno = options_raw[-1][0]
    for j in range(last_option_lineno + 1, min(last_option_lineno + 6, len(lines))):
        candidate = lines[j].strip()
        if _FOOTER_RE.search(candidate):
            footer = candidate
            break

    options = tuple(
        ChoiceOption(
            number=num,
            label=label,
            description=option_descs.get(num, ""),
            checked=checked,
        )
        for _lineno, _has_cursor, num, label, checked in options_raw
    )
    return MultipleChoicePrompt(
        question=question,
        options=options,
        selected_index=selected_index,
        footer=footer,
        multi_select=any(o.checked is not None for o in options),
        submit_selected=submit_selected,
    )


__all__ = [
    "ChoiceOption",
    "MultipleChoicePrompt",
    "parse_claude_code_choice_prompt",
]
