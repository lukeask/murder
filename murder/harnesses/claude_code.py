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
    UsageCollectionMode,
)
from murder.harnesses.models import HarnessUsageStatus
from murder.harnesses.parsing import (
    extract_last_message_heuristic,
    strip_ansi,
)
from murder.harnesses.results import SimpleResult, fail_result, ok_result
from murder.harnesses.usage import parse_claude_usage_pane


class ClaudeCodeAdapter(HarnessAdapter):
    kind: ClassVar[str] = "claude_code"
    usage_collection_mode: ClassVar[UsageCollectionMode] = "tmux_slash"
    # CC's `/model` opens a radio dialog of human labels ("Default", "Opus ✔",
    # "Haiku"), not `--model` ids — the hardcoded list below is the source of
    # truth, so skip `/model` discovery entirely.
    model_list_command: ClassVar[str | None] = None
    # Transcript parsing (best-effort; fixture: tests/fixtures/harness_panes/
    # claude_transcript.txt). CC echoes the submitted prompt on a "> …" / "❯ …"
    # line; the reply (●) and tool boxes (⏺ ⎿) follow until the next prompt.
    # Status-bar and "✻ …" spinner lines are dropped; the empty trailing "❯ "
    # and the pre-first-message placeholder both parse to no turn.
    transcript_prompt_markers: ClassVar[tuple[str, ...]] = (">", "❯")
    transcript_drop_substrings: ClassVar[tuple[str, ...]] = (
        "bypass permissions",
        "esc to interrupt",
        "for shortcuts",
        "? for help",
        " · ~/",
    )

    # Claude Code prompt is ">" or "? " depending on version/context.
    # We also accept any non-empty pane after seeing the banner so startup
    # doesn't hang for 240 s if the regex misses a prompt variant.
    _READY_RE = re.compile(
        r"[>?❯]\s*$"  # bare prompt at end of line
        r"|✓\s+claude"  # "✓ claude@api" banner line
        r"|Welcome to Claude"  # older banner
        r"|claude\.ai"  # footer URL
        r"|Claude Code\b"  # version banner (CC 2.x: "Claude Code v2.1.x")
        r"|bypass permissions",  # skip-permissions status bar (CC 2.x)
        re.MULTILINE | re.IGNORECASE,
    )
    _IDLE_RE = re.compile(r"[>?❯]\s*$", re.MULTILINE)
    _BUSY_RE = re.compile(r"(?:thinking|Esc to interrupt|\.{3})", re.IGNORECASE)
    _CC2_UI_RE = re.compile(r"bypass permissions", re.IGNORECASE)
    # CC 2.x: "Esc to interrupt" appears in status bar ONLY while actively generating.
    # _BUSY_RE includes \.{3} which would false-positive on placeholder text, so use this.
    _CC2_GENERATING_RE = re.compile(r"Esc to interrupt", re.IGNORECASE)
    # First launch in an un-trusted directory: CC shows an interactive
    # "do you trust the files in this folder?" list dialog. Our ready-regex
    # matches that dialog (it contains "Claude Code") so startup proceeds, but
    # nothing dismisses it — initialize_defaults() answers it.
    _TRUST_PROMPT_RE = re.compile(
        r"trust the files in this folder|Yes, I trust this folder|trust this folder\?",
        re.IGNORECASE,
    )

    crow_system_prompt: ClassVar[str] = "see prompts/crow_claude_code.md"
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
        clean = strip_ansi(pane_text)
        # CC 2.x shows "bypass permissions" in the status bar at all times;
        # "Esc to interrupt" is only added while actively generating.
        if self._CC2_UI_RE.search(clean):
            return not bool(self._CC2_GENERATING_RE.search(clean))
        return bool(self._IDLE_RE.search(clean))

    def is_busy(self, pane_text: str) -> bool:
        return bool(self._BUSY_RE.search(strip_ansi(pane_text)))

    async def initialize_defaults(self, session, spec):  # type: ignore[override]
        del spec
        # Dismiss the first-run "trust this folder?" dialog if it's up. The
        # ready check may have fired before the dialog rendered, so poll a bit.
        for _ in range(15):  # ~6 s
            try:
                pane = strip_ansi(await tmux.capture_pane(session, lines=40))
            except tmux.TmuxError:
                return ok_result()  # session gone; let the next wait_idle report it
            if self._TRUST_PROMPT_RE.search(pane):
                await tmux.send_keys(session, "1", literal=True, enter=True)
                await asyncio.sleep(0.6)
                return ok_result()
            if self.is_idle(pane):  # already at the REPL — nothing to dismiss
                return ok_result()
            await asyncio.sleep(0.4)
        return ok_result()

    def extract_last_message(self, pane_text: str) -> str | None:
        return extract_last_message_heuristic(pane_text)

    def format_nudge(self, msg: str) -> str:
        return f"[supervisor] {msg}"

    async def set_model(self, session: str, model: str) -> bool:
        del session
        return model == self.startup_model

    async def request_usage_status(self, session: str) -> bool:
        # If a prior /usage or /status dialog is still open, dismiss it first
        # so the slash command is submitted at the main prompt.
        await tmux.send_keys(session, "Escape", literal=False, enter=False)
        await asyncio.sleep(0.1)
        await tmux.send_keys(session, "/usage", literal=True, enter=True)
        await asyncio.sleep(0.2)
        return True

    async def collect_usage_status(self, session: str) -> SimpleResult[HarnessUsageStatus]:
        for attempt in range(2):
            await self.request_usage_status(session)
            await asyncio.sleep(0.4)
            pane = await tmux.capture_pane(session, lines=160)
            status = parse_claude_usage_pane(pane)
            has_session = status.session is not None and any(
                value is not None
                for value in (
                    status.session.input_tokens,
                    status.session.output_tokens,
                    status.session.cache_read_tokens,
                    status.session.cache_write_tokens,
                    status.session.cost_usd,
                    status.session.api_duration_s,
                    status.session.wall_duration_s,
                    status.session.lines_added,
                    status.session.lines_removed,
                )
            )
            if status.windows or has_session:
                return ok_result(status)
            if attempt == 0:
                await asyncio.sleep(0.4)
        return fail_result("claude /usage did not expose any usage details")
