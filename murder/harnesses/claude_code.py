"""Claude Code CLI adapter (`claude --dangerously-skip-permissions`).

Caveats:
- `--dangerously-skip-permissions` refuses to run as root; surfaced in
  `murder doctor`.
- CC's UI has tool-box rendering and spinners; pane regexes need
  empirical tuning during M1/M2.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import ClassVar

from murder import tmux
from murder.harnesses.base import (
    HarnessAdapter,
)
from murder.harnesses.models import HarnessUsageStatus
from murder.harnesses.parsing import (
    extract_last_message_heuristic,
    strip_ansi,
)
from murder.harnesses.results import SimpleResult, ok_result
from murder.harnesses.usage import parse_claude_usage_pane


class ClaudeCodeAdapter(HarnessAdapter):
    kind: ClassVar[str] = "claude_code"

    # Claude Code prompt is ">" or "? " depending on version/context.
    # We also accept any non-empty pane after seeing the banner so startup
    # doesn't hang for 240 s if the regex misses a prompt variant.
    _READY_RE = re.compile(
        r"[>?❯]\s*$"           # bare prompt at end of line
        r"|✓\s+claude"         # "✓ claude@api" banner line
        r"|Welcome to Claude"  # older banner
        r"|claude\.ai",        # footer URL
        re.MULTILINE | re.IGNORECASE,
    )
    _IDLE_RE = re.compile(r"[>?❯]\s*$", re.MULTILINE)
    _BUSY_RE = re.compile(r"(?:thinking|Esc to interrupt|\.{3})", re.IGNORECASE)

    monkey_system_prompt: ClassVar[str] = "see prompts/monkey_claude_code.md"
    available_startup_models: ClassVar[list[tuple[str, str]]] = [
        ("opus", "Opus"),
        ("sonnet", "Sonnet"),
        ("haiku", "Haiku"),
    ]

    def startup_cmd(self, cwd: Path) -> list[str]:
        cmd = ["claude", "--dangerously-skip-permissions"]
        if self.startup_model:
            cmd.extend(["--model", self.startup_model])
        return cmd

    def is_ready(self, pane_text: str) -> bool:
        return bool(self._READY_RE.search(strip_ansi(pane_text)))

    def is_idle(self, pane_text: str) -> bool:
        return bool(self._IDLE_RE.search(strip_ansi(pane_text)))

    def is_busy(self, pane_text: str) -> bool:
        return bool(self._BUSY_RE.search(strip_ansi(pane_text)))

    def extract_last_message(self, pane_text: str) -> str | None:
        return extract_last_message_heuristic(pane_text)

    def format_nudge(self, msg: str) -> str:
        return f"[supervisor] {msg}"

    async def set_model(self, session: str, model: str) -> bool:
        del session
        return model == self.startup_model

    async def request_usage_status(self, session: str) -> bool:
        await tmux.send_keys(session, "/usage", literal=True, enter=True)
        await asyncio.sleep(0.2)
        return True

    async def collect_usage_status(
        self, session: str
    ) -> SimpleResult[HarnessUsageStatus]:
        await self.request_usage_status(session)
        await asyncio.sleep(0.4)
        pane = await tmux.capture_pane(session, lines=160)
        return ok_result(parse_claude_usage_pane(pane))
