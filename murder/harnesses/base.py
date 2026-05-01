"""HarnessAdapter ABC + shared helpers."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar

ASK_RE = re.compile(r">>>\s*ASK:\s*(?P<body>.+?)(?=\n>>>|\Z)", re.DOTALL)
CHECK_RE = re.compile(r">>>\s*CHECK:\s*(?P<body>.+?)$", re.MULTILINE)
NOTE_RE = re.compile(r">>>\s*NOTE:\s*(?P<body>.+?)(?=\n>>>|\Z)", re.DOTALL)
DONE_RE = re.compile(r">>>\s*DONE\b")

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def extract_last_message_heuristic(pane_text: str, *, max_lines: int = 40) -> str | None:
    """Best-effort last user-visible block (bottom of pane), for Augur summaries."""
    lines = [ln.rstrip() for ln in strip_ansi(pane_text).splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return None
    block: list[str] = []
    for ln in reversed(lines[-max_lines:]):
        s = ln.strip()
        if not s:
            if block:
                break
            continue
        # Drop bare prompt-ish tails
        if s in (">", "$", "%", "#") or len(s) == 1 and s in ">#$%":
            if block:
                break
            continue
        block.append(ln)
    if not block:
        return None
    block.reverse()
    return "\n".join(block).strip() or None


class HarnessAdapter(ABC):
    kind: ClassVar[str]
    monkey_system_prompt: ClassVar[str]

    @abstractmethod
    def startup_cmd(self, cwd: Path) -> list[str]: ...

    @abstractmethod
    def is_ready(self, pane_text: str) -> bool: ...

    @abstractmethod
    def is_idle(self, pane_text: str) -> bool: ...

    @abstractmethod
    def is_busy(self, pane_text: str) -> bool: ...

    async def send_prompt(self, session: str, prompt: str) -> None:
        """Default: tmux.send_keys with literal mode (D10 large-payload aware)."""
        from murder.tmux import send_keys

        await send_keys(session, prompt, literal=True, enter=True)

    @abstractmethod
    def extract_last_message(self, pane_text: str) -> str | None: ...

    def detect_ask(self, pane_text: str) -> str | None:
        m = ASK_RE.search(pane_text)
        return m.group("body").strip() if m else None

    def detect_asks(self, pane_text: str) -> list[str]:
        return [m.group("body").strip() for m in ASK_RE.finditer(pane_text)]

    def detect_checks(self, pane_text: str) -> list[str]:
        return [m.group("body").strip() for m in CHECK_RE.finditer(pane_text)]

    def detect_notes(self, pane_text: str) -> list[str]:
        return [m.group("body").strip() for m in NOTE_RE.finditer(pane_text)]

    def detect_done(self, pane_text: str) -> bool:
        return bool(DONE_RE.search(pane_text))

    @abstractmethod
    def format_nudge(self, msg: str) -> str: ...

    async def interrupt(self, session: str) -> None:
        """Default: Ctrl-C the pane."""
        from murder.tmux import interrupt as tmux_interrupt

        await tmux_interrupt(session)
