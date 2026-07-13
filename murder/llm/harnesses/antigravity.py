"""Antigravity CLI adapter (`agy --dangerously-skip-permissions`).

Pane regexes were checked against `agy 1.0.2` on 2026-05-28 using the
recordings under ``tools/testing/recordings/20260527-21*-agy-*``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from murder.llm.harnesses.base import HarnessAdapter, UsageCollectionMode
from murder.llm.harnesses.models import HarnessModelState
from murder.llm.harnesses.parsing import (
    extract_last_message_heuristic,
    normalize_effort,
    slug_model_label,
    strip_ansi,
)

_TAIL_LINES = 25
_BANNER_RE = re.compile(r"Antigravity CLI\s+\d", re.IGNORECASE)
_SIGNING_IN_RE = re.compile(r"Signing in", re.IGNORECASE)
_IDLE_FOOTER_RE = re.compile(r"\?\s*for shortcuts", re.IGNORECASE)
_MODAL_FOOTER_RE = re.compile(r"esc to cancel", re.IGNORECASE)
# The agy busy spinner's verb drifts across releases: agy 1.0.2 painted
# "Generating...", agy 1.0.10 paints "Loading..." (other gerunds appear on tool
# turns). Match the live spinner line — a braille spinner glyph followed by a
# gerund + "..." — for ANY verb, so a future rename still reads busy. The braille
# glyph anchors it to the real spinner; we deliberately do NOT key busy off the
# modal footer "esc to cancel" alone, because the /model picker shares that exact
# footer (it would then read busy). Verified live against agy 1.0.10 on
# 2026-06-23 ("⡿  Loading..."). The footer-based idle check (is_idle requires
# "? for shortcuts", absent while busy) is the complementary guard.
_BUSY_RE = re.compile(r"[⠀-⣿]\s+\w+\.\.\.", re.UNICODE)
_AGY_STATUS_MODEL_RE = re.compile(
    r"(?P<label>(?:Gemini|Claude|GPT|Sonnet|Opus)[^\n]{2,60}?"
    r"\((?:Low|Medium|High|Thinking)\))",
    re.IGNORECASE,
)
_TRUST_PROMPT_RE = re.compile(
    r"Do you trust the contents|Yes, I trust this folder",
    re.IGNORECASE,
)


def _tail(pane_text: str) -> str:
    lines = pane_text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines[-_TAIL_LINES:])


def _agy_label_parts(label: str) -> tuple[str, str | None]:
    clean = re.sub(r"\s+", " ", label).strip()
    effort_match = re.search(r"\((Low|Medium|High|Thinking)\)", clean, re.IGNORECASE)
    effort = normalize_effort(effort_match.group(1)) if effort_match else None
    base = re.sub(r"\([^)]+\)", "", clean).strip()
    return slug_model_label(base), effort


class AntigravityAdapter(HarnessAdapter):
    kind: ClassVar[str] = "antigravity"
    crow_system_prompt: ClassVar[str] = "see prompts/crow_antigravity.md"
    usage_collection_mode: ClassVar[UsageCollectionMode] = "tmux_slash"
    supported_efforts: ClassVar[tuple[str, ...]] = ("low", "medium", "high")
    default_effort: ClassVar[str] = "medium"
    available_startup_models: ClassVar[list[tuple[str, str]]] = []

    def startup_cmd(self, cwd: Path) -> list[str]:
        del cwd
        return ["agy", "--dangerously-skip-permissions"]

    def is_ready(self, pane_text: str) -> bool:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _SIGNING_IN_RE.search(tail):
            return False
        if _TRUST_PROMPT_RE.search(tail):
            return True
        return bool(
            _IDLE_FOOTER_RE.search(tail) or _MODAL_FOOTER_RE.search(tail) or _BANNER_RE.search(tail)
        )

    def is_idle(self, pane_text: str) -> bool:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _TRUST_PROMPT_RE.search(tail) or _BUSY_RE.search(tail):
            return False
        return bool(_IDLE_FOOTER_RE.search(tail))

    def is_busy(self, pane_text: str) -> bool:
        return bool(_BUSY_RE.search(_tail(strip_ansi(pane_text))))

    def extract_last_message(self, pane_text: str) -> str | None:
        return extract_last_message_heuristic(pane_text)

    def parse_active_model_state(self, pane_text: str) -> HarnessModelState | None:
        clean = strip_ansi(pane_text)
        matches = list(_AGY_STATUS_MODEL_RE.finditer(clean))
        if not matches:
            return None
        label = matches[-1].group("label").strip()
        model_id, effort = _agy_label_parts(label)
        if not model_id:
            return None
        return HarnessModelState(model=model_id, effort=effort)
