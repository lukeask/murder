"""Ticket markdown parser/writer (prose body only — frontmatter dropped per D9)."""

from __future__ import annotations

import re
from pathlib import Path

from murder.state.storage.filesystem import atomic_write_text

_KNOWN_SECTIONS = ("Plan", "Working notes")
_HEADER_RE = re.compile(r"^## (?P<name>.+?)\s*$", re.MULTILINE)


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
