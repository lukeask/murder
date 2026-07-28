"""Geometry-aware semantic boundaries for Codex ``/status`` terminal output.

This module deliberately knows about Codex's labelled status surface, but not
about tmux capture commands, viewport selection, or any UI renderer. Consumers
may supply a complete capture at the compatibility 220-column width or a
capture whose physical rows were wrapped at another width; the exported
logical rows have the same semantic shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from murder.llm.harnesses.parsing import strip_ansi

_HEADING_RE = re.compile(r"(?:>_\s*)?OpenAI\s+Codex\s*\(v[^)]*\)", re.I)
_FIELD_RE = re.compile(r"^(?P<label>[A-Za-z][A-Za-z ._-]{1,40}):\s*(?P<value>.+)$")
_STRUCTURED_ROW_RE = re.compile(
    r"^(?P<label>[A-Za-z0-9][A-Za-z0-9 ._/-]{1,40}):(?:\s+(?P<value>.*))?$"
)


@dataclass(frozen=True, slots=True)
class CodexStatusSurface:
    """One structurally bounded Codex status panel in physical-line coordinates."""

    start: int
    end: int
    complete: bool
    active: bool
    lines: tuple[str, ...]


def clean_codex_status_line(line: str) -> str:
    return line.strip().strip("│").strip()


def fold_codex_status_lines(lines: Sequence[str]) -> list[str]:
    """Fold wrapped colon-labelled fields into explicit logical rows.

    Only a labelled row owns continuations. Blank lines, panel boundaries, and
    the next label terminate it, preventing content from an adjacent viewport
    region or historical panel from being concatenated accidentally. A clock
    continuation such as ``14:49 on 18 May`` is not a label because the colon
    is not followed by whitespace.
    """

    logical: list[str] = []
    current: str | None = None
    for raw in lines:
        line = clean_codex_status_line(raw)
        if not line or line.startswith(("╭", "╰", "+-", ">_ OpenAI Codex")):
            if current is not None:
                logical.append(current)
                current = None
            if line and not line.startswith(("╭", "╰", "+-")):
                logical.append(line)
            continue
        if _STRUCTURED_ROW_RE.match(line):
            if current is not None:
                logical.append(current)
            current = line
            continue
        if current is not None:
            current = f"{current.rstrip()} {line.lstrip()}"
        else:
            logical.append(line)
    if current is not None:
        logical.append(current)
    return logical


def find_codex_status_surfaces(pane_text: str) -> tuple[CodexStatusSurface, ...]:
    """Return every structurally valid status panel, oldest to newest."""

    lines = strip_ansi(pane_text).splitlines()
    last_composer = max(
        (index for index, line in enumerate(lines) if line.lstrip().startswith("›")),
        default=-1,
    )
    candidates: list[CodexStatusSurface] = []
    for start, line in enumerate(lines):
        if not _HEADING_RE.search(line):
            continue
        next_heading = next(
            (index for index in range(start + 1, len(lines)) if _HEADING_RE.search(lines[index])),
            len(lines),
        )
        close = next(
            (
                index
                for index in range(start + 1, next_heading)
                if lines[index].lstrip().startswith(("╰", "+"))
            ),
            None,
        )
        end = (close + 1) if close is not None else next_heading
        content = fold_codex_status_lines(lines[start + 1 : end])
        labels = {
            match.group("label").strip().lower()
            for item in content
            if (match := _FIELD_RE.match(item)) is not None
        }
        if not labels.intersection({"session", "account", "collaboration mode"}):
            continue
        # Borderless captures are complete only when a later heading/composer
        # bounds them. The final unbounded render remains fail-closed.
        complete = close is not None or end < len(lines)
        candidates.append(
            CodexStatusSurface(
                start=start,
                end=end,
                complete=complete,
                active=start > last_composer,
                lines=tuple(lines[start:end]),
            )
        )
    return tuple(candidates)


def locate_codex_status_surface(pane_text: str) -> CodexStatusSurface | None:
    """Select the newest complete status surface, falling back to newest partial."""

    candidates = find_codex_status_surfaces(pane_text)
    completed = [candidate for candidate in candidates if candidate.complete]
    return (completed or list(candidates) or [None])[-1]


def codex_status_physical_lines(lines: Sequence[str]) -> frozenset[int]:
    """Physical line indexes belonging to status panels for transcript masking."""

    text = "\n".join(lines)
    return frozenset(
        index
        for surface in find_codex_status_surfaces(text)
        for index in range(surface.start, surface.end)
    )


__all__ = [
    "CodexStatusSurface",
    "clean_codex_status_line",
    "codex_status_physical_lines",
    "find_codex_status_surfaces",
    "fold_codex_status_lines",
    "locate_codex_status_surface",
]
