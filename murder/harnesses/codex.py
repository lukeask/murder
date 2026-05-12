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
from murder.harnesses.results import SimpleResult, ok_result
from murder.harnesses.usage import parse_codex_status_pane

_TAIL_LINES = 30

_BANNER_RE = re.compile(r"OpenAI Codex", re.IGNORECASE)
# The Codex input box renders as a "› …" line; the placeholder text after it
# rotates ("Explain this codebase", "Find and fix a bug in @filename", …), so
# match any "› " line (busy state is screened separately, before this check).
_IDLE_PROMPT_RE = re.compile(r"^\s*›(?:\s.*)?$", re.MULTILINE)
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
    usage_collection_mode: ClassVar[UsageCollectionMode] = "tmux_slash"
    # Codex's model picker is `/model` (singular); it opens a numbered modal
    # list (`› 1. gpt-5.5 (current)  Frontier model …`) which the generic
    # parser handles. The modal needs a beat to render, so capture late.
    model_list_command: ClassVar[str | None] = "/model"
    model_list_capture_delay_s: ClassVar[float] = 3.0
    # Transcript parsing (best-effort; fixture: tests/fixtures/harness_panes/
    # codex_transcript.txt). Codex echoes the submitted prompt on a "› …" line;
    # the reply follows on "• …" lines. The footer placeholder ("Find and fix
    # a bug in @filename" etc.) is the live input box — its trailing status bar
    # ("<model> <effort> · ~/<cwd>") is dropped so that turn parses as empty
    # and is discarded.
    transcript_prompt_markers: ClassVar[tuple[str, ...]] = ("›",)
    transcript_drop_substrings: ClassVar[tuple[str, ...]] = (
        "esc to interrupt",
        "to interrupt",
        "tokens used",
        "openai codex",
        "use /permissions",
        " · ~/",
    )
    crow_system_prompt: ClassVar[str] = "see prompts/crow_codex.md"
    available_startup_models: ClassVar[list[tuple[str, str]]] = [
        ("gpt-5.5", "GPT-5.5"),
        ("gpt-5.4", "GPT-5.4"),
        ("gpt-5.4-mini", "GPT-5.4 Mini"),
        ("gpt-5.3-codex", "GPT-5.3 Codex"),
        ("gpt-5.2", "GPT-5.2"),
    ]

    def startup_cmd(self, cwd: Path) -> list[str]:
        cmd = [
            "codex",
            "--no-alt-screen",
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
        ]
        if self.startup_model:
            cmd.extend(["--model", self.startup_model])
        return cmd

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

    async def set_model(self, session: str, model: str) -> bool:
        del session
        return model == self.startup_model

    async def request_usage_status(self, session: str) -> bool:
        await tmux.send_keys(session, "/status", literal=True, enter=True)
        await asyncio.sleep(0.2)
        return True

    async def collect_usage_status(
        self, session: str
    ) -> SimpleResult[HarnessUsageStatus]:
        await self.request_usage_status(session)
        await asyncio.sleep(0.4)
        pane = await tmux.capture_pane(session, lines=160)
        return ok_result(parse_codex_status_pane(pane))
