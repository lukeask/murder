"""Ticket markdown parser/writer."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from murder.state.storage.filesystem import atomic_write_text

_FRONTMATTER_DELIM = "---"
_MISSING_CLOSE_ERROR = "ticket markdown is missing closing frontmatter delimiter"
_CANONICAL_FRONTMATTER_KEYS = ("title", "deps", "harness", "model", "worktree")
_ALIASES = {
    "dependency": "deps",
    "dependencies": "deps",
    "depends_on": "deps",
    "harness_override": "harness",
}
_KNOWN_SECTIONS = ("Plan", "Working notes")
_HEADER_RE = re.compile(r"^## (?P<name>.+?)\s*$", re.MULTILINE)
_CHECKLIST_HEADER_RE = re.compile(r"^# Checklist\s*$")
_LEVEL_ONE_HEADER_RE = re.compile(r"^# (?!#).+?\s*$")
_CHECKLIST_ITEM_RE = re.compile(r"^\[(?P<mark> |x|X)\]\s+(?P<text>.+?)\s*$")


@dataclass(frozen=True, slots=True)
class TicketChecklistItem:
    text: str
    done: bool = False


@dataclass(frozen=True, slots=True)
class ParsedTicket:
    title: str | None = None
    deps: list[str] = field(default_factory=list)
    harness: str | None = None
    model: str | None = None
    worktree: str | None = None
    body: str = ""
    checklist: list[TicketChecklistItem] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)
    parse_error: str | None = None


def parse_ticket(md_text: str, *, default_title: str | None = None) -> ParsedTicket:
    """Parse unified ticket markdown without raising on malformed input."""
    try:
        front_text, body, delimiter_error = _split_frontmatter(md_text)
        raw: dict[str, Any] = {}
        errors: list[str] = []
        # The planner writes a frontmatter-less ticket `.md` by convention (see
        # prompts/planner.md): three prose sections, no `---` block. That file
        # MUST still ingest to a `planned` row (the carve form fills harness/
        # model/title later). So a total *absence* of frontmatter is not an
        # error — only a *malformed* (opened-but-unclosed / non-mapping) block
        # is. ``_split_frontmatter`` distinguishes the two: ``has_frontmatter``
        # is False with no delimiter_error when the file simply has no `---`.
        has_frontmatter = front_text is not None
        frontmatter_required = has_frontmatter or delimiter_error == _MISSING_CLOSE_ERROR
        if delimiter_error == _MISSING_CLOSE_ERROR:
            errors.append(delimiter_error)
        elif front_text is not None:
            try:
                loaded = yaml.safe_load(front_text) or {}
            except yaml.YAMLError as exc:
                loaded = {}
                errors.append(f"invalid ticket frontmatter YAML: {exc}")
            if not isinstance(loaded, dict):
                errors.append("ticket frontmatter must be a mapping")
            else:
                raw = {str(key): value for key, value in loaded.items()}

        normalized = _normalize_aliases(raw)
        known = {key: normalized.get(key) for key in _CANONICAL_FRONTMATTER_KEYS}
        extras = {
            key: value
            for key, value in normalized.items()
            if key not in _CANONICAL_FRONTMATTER_KEYS
        }

        title = _optional_non_empty_str(known.get("title"))
        if title is None and not frontmatter_required:
            # Frontmatter-less tickets (planner files, `ticket.quick_create`) carry
            # the title in the leading `# {title}` heading. Recover it so a reconcile
            # doesn't clobber the real title with the id fallback. Only applied when
            # no frontmatter block is present — a frontmatter ticket's title MUST come
            # from the `title:` field (a missing one is a reportable error, not a
            # heading scrape).
            title = _heading_title(body)
        if title is None:
            title = _optional_non_empty_str(default_title)
        if title is None and frontmatter_required:
            errors.append("ticket frontmatter requires a non-empty title")

        deps = _coerce_str_list(known.get("deps"), "deps", errors)
        harness = _optional_string_field(known.get("harness"), "harness", errors)
        model = _optional_string_field(known.get("model"), "model", errors)
        worktree = _optional_string_field(known.get("worktree"), "worktree", errors)
        # harness/model are only *required* when the author chose to write a
        # frontmatter block. A frontmatter-less planner file leaves them null;
        # they are supplied later by the carve form (apply_carve_ready).
        if harness is None and frontmatter_required:
            errors.append("ticket frontmatter requires a non-empty harness")
        if model is None and frontmatter_required:
            errors.append("ticket frontmatter requires a non-empty model")

        return ParsedTicket(
            title=title,
            deps=deps,
            harness=harness,
            model=model,
            worktree=worktree,
            body=body,
            checklist=_parse_checklist(body),
            extras=extras,
            parse_error="; ".join(errors) or None,
        )
    except Exception as exc:  # pragma: no cover - defensive contract guard.
        return ParsedTicket(parse_error=f"unexpected ticket parse error: {exc}")


def _split_frontmatter(md_text: str) -> tuple[str | None, str, str | None]:
    if not md_text.startswith(f"{_FRONTMATTER_DELIM}\n"):
        # No frontmatter at all: valid (the planner writes such files). The
        # caller treats this as "frontmatter-less", NOT as a parse error.
        return None, md_text, None
    try:
        front_text, body = md_text[4:].split(f"\n{_FRONTMATTER_DELIM}", 1)
    except ValueError:
        return None, md_text, _MISSING_CLOSE_ERROR
    if body.startswith("\n"):
        body = body[1:]
    return front_text, body, None


def _normalize_aliases(raw: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in raw.items():
        canonical = _ALIASES.get(key, key)
        if canonical in normalized:
            continue
        normalized[canonical] = value
    return normalized


def _heading_title(body: str) -> str | None:
    """Recover a title from the leading `# {title}` heading of a frontmatter-less ticket.

    Skips the `# Checklist` section header (matched by ``_CHECKLIST_HEADER_RE``),
    which is structural, not a title. Returns the first real level-one heading's
    text, or None if there is none.
    """
    for line in body.splitlines():
        if _CHECKLIST_HEADER_RE.match(line):
            continue
        if _LEVEL_ONE_HEADER_RE.match(line):
            return _optional_non_empty_str(line[1:])
    return None


def _optional_non_empty_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _optional_string_field(value: object, field_name: str, errors: list[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append(f"{field_name} must be a string when present")
        return None
    stripped = value.strip()
    return stripped or None


def _coerce_str_list(value: object, field_name: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, tuple):
        value = list(value)
    if not isinstance(value, list):
        errors.append(f"{field_name} must be a list of strings")
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                result.append(stripped)
        else:
            errors.append(f"{field_name} must be a list of strings")
            return []
    return result


def _parse_checklist(body: str) -> list[TicketChecklistItem]:
    lines = body.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if _CHECKLIST_HEADER_RE.match(line.strip()):
            start = index + 1
            break
    if start is None:
        return []

    items: list[TicketChecklistItem] = []
    for line in lines[start:]:
        stripped = line.strip()
        if _LEVEL_ONE_HEADER_RE.match(stripped):
            break
        match = _CHECKLIST_ITEM_RE.match(stripped)
        if match is None:
            continue
        items.append(
            TicketChecklistItem(
                text=match.group("text").strip(),
                done=match.group("mark").lower() == "x",
            )
        )
    return items


def parse(md_text: str) -> dict[str, str]:
    """Split body into known sections. Unknown sections / preamble go in `_preamble`.

    Returns dict with keys 'plan', 'working_notes', '_preamble'.
    """
    sections: dict[str, str] = {
        "plan": "",
        "working_notes": "",
        "_preamble": "",
    }
    matches = list(_HEADER_RE.finditer(md_text))
    if not matches:
        sections["_preamble"] = md_text.strip()
        return sections
    if matches[0].start() > 0:
        sections["_preamble"] = md_text[: matches[0].start()].strip()
    for i, m in enumerate(matches):
        name = m.group("name").strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(md_text)
        body = md_text[body_start:body_end].strip()
        if name == "Plan":
            sections["plan"] = body
        elif name == "Working notes":
            sections["working_notes"] = body
        # Unknown sections are silently dropped in v0.
    return sections


def render(plan: str = "", working_notes: str = "") -> str:
    """Emit a canonical ticket markdown body."""
    parts = [
        "## Plan",
        plan.strip() or "_(empty)_",
        "",
        "## Working notes",
        working_notes.strip(),
        "",
    ]
    return "\n".join(parts).rstrip() + "\n"


def read_ticket_md(path: Path) -> dict[str, str]:
    return parse(path.read_text(encoding="utf-8"))


def write_ticket_md(path: Path, sections: dict[str, str]) -> None:
    """Atomic write — only the two known sections are written."""
    text = render(
        plan=sections.get("plan", ""),
        working_notes=sections.get("working_notes", ""),
    )
    atomic_write_text(path, text)


def append_section(path: Path, section: str, text: str) -> None:
    """Append `text` to `section` ('Plan' or 'Working notes').

    Creates the file or section if missing. Atomic.
    """
    if section not in _KNOWN_SECTIONS:
        raise ValueError(f"unknown section: {section!r}")
    if path.exists():
        sections = parse(path.read_text(encoding="utf-8"))
    else:
        sections = {"plan": "", "working_notes": "", "_preamble": ""}
    key = section.lower().replace(" ", "_")
    existing = sections.get(key, "")
    sections[key] = (existing + ("\n\n" if existing else "") + text.strip()).strip()
    write_ticket_md(path, sections)
