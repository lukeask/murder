"""Pi coding-agent adapter (`pi`).

Pi's README and installed package docs were checked on 2026-05-02. The
adapter uses the CLI `--model` startup flag when a preferred startup model is
configured, because Pi's interactive `/model` command opens a selector UI.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from murder.harnesses.base import (
    HarnessAdapter,
)
from murder.harnesses.parsing import (
    extract_last_message_heuristic,
    strip_ansi,
)

_TAIL_LINES = 30

_READY_RE = re.compile(
    r"(/hotkeys|Ctrl\+L|Ctrl\+P|Slash commands|^\s*[>/]\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_IDLE_RE = re.compile(
    r"(/hotkeys|Ctrl\+L|Ctrl\+P|^\s*[>/]\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_BUSY_RE = re.compile(
    r"\b(thinking|streaming|running|executing|tool calls?|retrying|compacting)\b",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"\b(login|authenticate|api key|required|no provider|configure provider)\b",
    re.IGNORECASE,
)


def _tail(pane_text: str) -> str:
    lines = pane_text.splitlines()
    return "\n".join(lines[-_TAIL_LINES:])


class PiAdapter(HarnessAdapter):
    kind: ClassVar[str] = "pi"
    monkey_system_prompt: ClassVar[str] = "see prompts/monkey_pi.md"
    available_startup_models: ClassVar[list[tuple[str, str]]] = [
        ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("anthropic/claude-opus-4-7", "Claude Opus 4.7"),
        ("openai/gpt-5.5", "GPT-5.5"),
        ("openai/gpt-5.4-mini", "GPT-5.4 Mini"),
    ]

    def startup_cmd(self, cwd: Path) -> list[str]:
        cmd = ["pi"]
        if self.startup_model:
            cmd.extend(["--model", self.startup_model])
        return cmd

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
        return extract_last_message_heuristic(pane_text)

    def format_nudge(self, msg: str) -> str:
        return f"[supervisor] {msg}"

    async def set_model(self, session: str, model: str) -> bool:
        del session
        return model == self.startup_model
