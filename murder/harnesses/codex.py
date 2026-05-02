"""Codex CLI adapter (`codex --no-alt-screen ...`).

Pane regexes were checked against `codex v0.128.0` on 2026-05-02. The
adapter runs Codex in inline mode so tmux capture-pane can see the live UI
instead of an alternate-screen buffer.
"""

from __future__ import annotations

import asyncio
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

_BANNER_RE = re.compile(r"OpenAI Codex", re.IGNORECASE)
_IDLE_PROMPT_RE = re.compile(r"^\s*›(?:\s+Explain this codebase)?\s*$", re.MULTILINE)
_BUSY_RE = re.compile(
    r"\b(working|thinking|running|executing|applying patch|processing)\b",
    re.IGNORECASE,
)
_LOGIN_RE = re.compile(r"\b(login required|not logged in|codex login)\b", re.IGNORECASE)


def _tail(pane_text: str) -> str:
    lines = pane_text.splitlines()
    return "\n".join(lines[-_TAIL_LINES:])


class CodexAdapter(HarnessAdapter):
    kind: ClassVar[str] = "codex"
    monkey_system_prompt: ClassVar[str] = "see prompts/monkey_codex.md"

    def startup_cmd(self, cwd: Path) -> list[str]:
        return [
            "codex",
            "--no-alt-screen",
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
        ]

    def is_ready(self, pane_text: str) -> bool:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _LOGIN_RE.search(tail):
            return False
        return bool(_BANNER_RE.search(clean) or _IDLE_PROMPT_RE.search(tail))

    def is_idle(self, pane_text: str) -> bool:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _LOGIN_RE.search(tail) or self.is_busy(tail):
            return False
        return bool(_IDLE_PROMPT_RE.search(tail))

    def is_busy(self, pane_text: str) -> bool:
        return bool(_BUSY_RE.search(_tail(strip_ansi(pane_text))))

    def extract_last_message(self, pane_text: str) -> str | None:
        return extract_last_message_heuristic(pane_text)

    def format_nudge(self, msg: str) -> str:
        return f"[supervisor] {msg}"

    async def request_usage_status(self, session: str) -> bool:
        from murder.tmux import send_keys

        await send_keys(session, "/status", literal=True, enter=True)
        await asyncio.sleep(0.2)
        return True
