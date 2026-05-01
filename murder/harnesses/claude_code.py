"""Claude Code CLI adapter (`claude --dangerously-skip-permissions`).

Caveats:
- `--dangerously-skip-permissions` refuses to run as root; surfaced in
  `murder doctor`.
- CC's UI has tool-box rendering and spinners; pane regexes need
  empirical tuning during M1/M2.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from murder.harnesses.base import (
    HarnessAdapter,
    extract_last_message_heuristic,
    strip_ansi,
)


class ClaudeCodeAdapter(HarnessAdapter):
    kind: ClassVar[str] = "claude_code"

    # TODO(M2): empirically pin against real CC pane captures.
    _READY_RE = re.compile(r"^>\s*$|claude>", re.MULTILINE | re.IGNORECASE)
    _IDLE_RE = re.compile(r"^>\s*$", re.MULTILINE)
    _BUSY_RE = re.compile(r"(?:thinking|tool|\.{3})", re.IGNORECASE)

    monkey_system_prompt: ClassVar[str] = "see prompts/monkey_claude_code.md"

    def startup_cmd(self, cwd: Path) -> list[str]:
        return ["claude", "--dangerously-skip-permissions"]

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
