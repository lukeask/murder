"""Pi coding-agent adapter (`pi`).

Pi's README and installed package docs were checked on 2026-05-02.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from murder.llm.harnesses.base import (
    HarnessAdapter,
)
from murder.llm.harnesses.models import HarnessModelState
from murder.llm.harnesses.parsing import (
    extract_last_message_heuristic,
    normalize_effort,
    strip_ansi,
)

# The pi chrome predicate and reasoning-prefix regex live in the grammar module
# (core/grammars import no adapter; adapter→grammar is the allowed direction).
from murder.llm.harnesses.transcripts.grammar.pi import (
    _PI_REASONING_PREFIX_RE,
    _is_pi_chrome,
)

_TAIL_LINES = 30
_MIN_REASONING_PARAGRAPHS = 2

_READY_RE = re.compile(
    r"(/hotkeys|Ctrl\+[A-Z]|Ctrl\+o|Slash commands|[\d.]+%/[\d.]+[kKmM]|^\s*[>/]\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_IDLE_RE = re.compile(
    r"(/hotkeys|Ctrl\+[A-Z]|Ctrl\+o|[\d.]+%/[\d.]+[kKmM]|^\s*[>/]\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_BUSY_RE = re.compile(
    r"\b(thinking|streaming|running|working|executing|tool calls?|retrying|compacting)\b",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"\b(login|authenticate|api key|required|no provider|configure provider)\b",
    re.IGNORECASE,
)
_PI_ACTIVE_MODEL_RE = re.compile(
    r"\((?P<provider>[a-z][a-z0-9_-]*)\)\s+"
    r"(?P<model>[A-Za-z0-9][A-Za-z0-9._+-]*)(?:\s*[•·]\s*(?P<effort>low|medium|high))?",
    re.IGNORECASE,
)


def _strip_pi_chrome(pane_text: str) -> str:
    return "\n".join(line for line in strip_ansi(pane_text).splitlines() if not _is_pi_chrome(line))


def _normalize_pi_transcript_body(role: str, body: str) -> str:
    if role != "assistant" or "\n\n" not in body:
        return body
    paragraphs = [part.strip() for part in body.split("\n\n") if part.strip()]
    if len(paragraphs) < _MIN_REASONING_PARAGRAPHS:
        return body
    if any(_PI_REASONING_PREFIX_RE.match(part) for part in paragraphs[:-1]):
        return paragraphs[-1]
    return body


def _tail(pane_text: str) -> str:
    lines = pane_text.splitlines()
    return "\n".join(lines[-_TAIL_LINES:])


class PiAdapter(HarnessAdapter):
    kind: ClassVar[str] = "pi"
    crow_system_prompt: ClassVar[str] = "see prompts/crow_pi.md"
    # Pi uses the transcripts grammar plugin for parsing; the generic
    # prompt-marker ClassVars remain unset and the UI falls back to the raw
    # pane mirror until those are configured.
    available_startup_models: ClassVar[list[tuple[str, str]]] = [
        ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("anthropic/claude-opus-4-7", "Claude Opus 4.7"),
        ("openai/gpt-5.5", "GPT-5.5"),
        ("openai/gpt-5.4-mini", "GPT-5.4 Mini"),
    ]

    def startup_cmd(self, cwd: Path) -> list[str]:
        del cwd
        return ["pi"]

    def is_ready(self, pane_text: str) -> bool:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _AUTH_RE.search(tail):
            return False
        return bool(_READY_RE.search(tail))

    def is_idle(self, pane_text: str) -> bool:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _AUTH_RE.search(tail) or self.is_busy(tail):
            return False
        return bool(_IDLE_RE.search(tail))

    def is_busy(self, pane_text: str) -> bool:
        return bool(_BUSY_RE.search(_tail(strip_ansi(pane_text))))

    def extract_last_message(self, pane_text: str) -> str | None:
        return extract_last_message_heuristic(_strip_pi_chrome(pane_text))

    def parse_active_model_state(self, pane_text: str) -> HarnessModelState | None:
        clean = strip_ansi(pane_text)
        for line in reversed(_tail(clean).splitlines()):
            match = _PI_ACTIVE_MODEL_RE.search(line)
            if match is None:
                continue
            provider = match.group("provider")
            short = match.group("model")
            model = f"{provider}/{short}"
            effort = normalize_effort(match.group("effort"))
            return HarnessModelState(model=model, effort=effort)
        return None
