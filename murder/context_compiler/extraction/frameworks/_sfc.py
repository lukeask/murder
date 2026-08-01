"""Shared single-file-component block splitting for Vue / Svelte extractors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

_TAG_OPEN_RE = re.compile(
    r"<(?P<name>script|template|style)(?P<attrs>[^>]*)>",
    re.IGNORECASE,
)
_SELF_CLOSE_RE = re.compile(r"/\s*$")


@dataclass(frozen=True, slots=True)
class SfcBlock:
    """One ``<script|template|style>`` block within an SFC."""

    name: str
    attrs: dict[str, str]
    content: str
    start_line: int
    end_line: int
    open_line: int
    # Content starts at this 1-based line within the file.
    content_start_line: int
    raw_attrs: str = ""


def _line_at(source: str, index: int) -> int:
    return source.count("\n", 0, index) + 1


def _parse_attrs(attr_text: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    # key="value" | key='value' | key=bare | bare-flag
    for match in re.finditer(
        r"""([:@A-Za-z_][\w:.-]*)(?:\s*=\s*(?:(['"])(.*?)\2|([^\s"'`=<>`]+)))?""",
        attr_text,
    ):
        key = match.group(1).lower()
        if match.group(3) is not None:
            attrs[key] = match.group(3)
        elif match.group(4) is not None:
            attrs[key] = match.group(4)
        else:
            attrs[key] = ""
    return attrs


def split_sfc_blocks(
    source: str, *, tags: tuple[str, ...] = ("script", "template", "style")
) -> list[SfcBlock]:
    """Split an SFC into named blocks with line ranges (best-effort regex)."""
    allowed = {t.lower() for t in tags}
    blocks: list[SfcBlock] = []
    pos = 0
    lower = source  # case handled via regex IGNORECASE
    while True:
        match = _TAG_OPEN_RE.search(lower, pos)
        if match is None:
            break
        name = match.group("name").lower()
        if name not in allowed:
            pos = match.end()
            continue
        attrs_raw = match.group("attrs") or ""
        if _SELF_CLOSE_RE.search(attrs_raw):
            pos = match.end()
            continue
        open_end = match.end()
        close_re = re.compile(rf"</{name}\s*>", re.IGNORECASE)
        close = close_re.search(source, open_end)
        if close is None:
            content = source[open_end:]
            end_index = len(source)
            pos = len(source)
        else:
            content = source[open_end : close.start()]
            end_index = close.end()
            pos = close.end()

        # Strip a single leading newline so script line 1 aligns with content.
        content_start_index = open_end
        if content.startswith("\r\n"):
            content = content[2:]
            content_start_index += 2
        elif content.startswith("\n"):
            content = content[1:]
            content_start_index += 1
        if content.endswith("\r\n"):
            content = content[:-2]
        elif content.endswith("\n"):
            content = content[:-1]

        blocks.append(
            SfcBlock(
                name=name,
                attrs=_parse_attrs(attrs_raw),
                content=content,
                start_line=_line_at(source, match.start()),
                end_line=_line_at(source, end_index - 1 if end_index else match.start()),
                open_line=_line_at(source, match.start()),
                content_start_line=_line_at(source, content_start_index),
                raw_attrs=attrs_raw.strip(),
            )
        )
    return blocks


def component_name_from_path(path: str) -> str:
    """Derive a PascalCase-ish component name from the file stem."""
    stem = PurePosixPath(path.replace("\\", "/")).stem
    if not stem:
        return "Component"
    # profile-card → ProfileCard; ProfileCard stays ProfileCard.
    if "-" in stem or "_" in stem:
        parts = re.split(r"[-_]+", stem)
        return "".join(p[:1].upper() + p[1:] for p in parts if p)
    if stem[0].islower():
        return stem[:1].upper() + stem[1:]
    return stem


def pascal_to_kebab(name: str) -> str:
    """ProfileCard → profile-card."""
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", s1).lower()


def shift_line(line: int | None, offset: int) -> int | None:
    if line is None:
        return None
    return line + offset


__all__ = [
    "SfcBlock",
    "component_name_from_path",
    "pascal_to_kebab",
    "shift_line",
    "split_sfc_blocks",
]
