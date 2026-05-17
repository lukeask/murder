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
from murder.harnesses.results import SimpleResult, fail_result, ok_result
from murder.harnesses.usage import parse_codex_status_pane

_TAIL_LINES = 30

_BANNER_RE = re.compile(r"OpenAI Codex", re.IGNORECASE)
# The Codex input box renders as a "› …" line; the placeholder text after it
# rotates ("Explain this codebase", "Find and fix a bug in @filename", …), so
# match any "› " line (busy state is screened separately, before this check).
_IDLE_PROMPT_RE = re.compile(r"^\s*›(?:\s.*)?$", re.MULTILINE)
_BUSY_RE = re.compile(
    r"^\s*(?:working|thinking|running|executing|processing|applying patch)\b",
    re.IGNORECASE | re.MULTILINE,
)
_LOGIN_RE = re.compile(r"\b(login required|not logged in|codex login)\b", re.IGNORECASE)

_STATUS_COMMAND_POPUP_DELAY_S = 0.5
_STATUS_FIRST_ENTER_DELAY_S = 0.8
_STATUS_CAPTURE_DELAY_S = 1.2
_STATUS_RETRY_DELAY_S = 0.6
_STATUS_DISMISS_DELAY_S = 0.1
_MODEL_STARTUP_SETTLE_DELAY_S = 1.5
_MODEL_COMMAND_POPUP_DELAY_S = 0.5
_MODEL_CAPTURE_DELAY_S = 3.0
_PROMPT_SUBMIT_DELAY_S = 0.2
# Codex inline composer expects Tab+Enter after each bracketed paste; long prompts
# must be split into multiple tmux pastes so each segment gets its own confirmation.
_CODEX_PASTE_CHUNK_UTF8 = 768


def _utf8_byte_chunks(data: bytes, max_bytes: int) -> list[bytes]:
    if max_bytes < 1:
        raise ValueError("max_bytes must be >= 1")
    chunks: list[bytes] = []
    i = 0
    n = len(data)
    while i < n:
        end = min(i + max_bytes, n)
        while end > i and end < n and (data[end] & 0xC0) == 0x80:
            end -= 1
        if end <= i:
            end = i + 1
        chunks.append(data[i:end])
        i = end
    return chunks


def _tail(pane_text: str) -> str:
    lines = pane_text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
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

    async def send_prompt(self, session: str, prompt: str) -> None:
        raw = prompt.encode("utf-8")
        if len(raw) < tmux.LARGE_PAYLOAD_BYTES:
            await tmux.send_keys(session, prompt, literal=True, enter=False)
            await asyncio.sleep(_PROMPT_SUBMIT_DELAY_S)
            await tmux.send_keys(session, "", literal=True, enter=True)
            return

        for piece in _utf8_byte_chunks(raw, _CODEX_PASTE_CHUNK_UTF8):
            await tmux.paste_buffer_literal(session, piece.decode("utf-8"))
            await asyncio.sleep(_PROMPT_SUBMIT_DELAY_S)
            await tmux.send_keys(session, "Tab", literal=False, enter=False)
            await tmux.send_keys(session, "", literal=True, enter=True)

    async def set_model(self, session: str, model: str) -> bool:
        del session
        return model == self.startup_model

    async def request_model_list(self, session: str) -> bool:
        # TODO: Replace this fixed delay with a pane-state wait for Codex to
        # fully finish startup; otherwise early slash commands can be rejected
        # as "disabled while a task is in progress."
        await asyncio.sleep(_MODEL_STARTUP_SETTLE_DELAY_S)
        await tmux.send_keys(session, "/model", literal=True, enter=False)
        # TODO: Replace this fixed delay with a pane-state wait for Codex's
        # slash-command picker, so model discovery is not timing-dependent.
        await asyncio.sleep(_MODEL_COMMAND_POPUP_DELAY_S)
        await tmux.send_keys(session, "", literal=True, enter=True)
        await asyncio.sleep(_MODEL_CAPTURE_DELAY_S)
        return True

    async def request_usage_status(self, session: str) -> bool:
        # If an older modal is still visible, close it before issuing /status.
        await tmux.send_keys(session, "Escape", literal=False, enter=False)
        await asyncio.sleep(_STATUS_DISMISS_DELAY_S)
        await tmux.send_keys(session, "/status", literal=True, enter=False)
        await asyncio.sleep(_STATUS_COMMAND_POPUP_DELAY_S)
        await tmux.send_keys(session, "", literal=True, enter=True)
        await asyncio.sleep(_STATUS_FIRST_ENTER_DELAY_S)
        await tmux.send_keys(session, "", literal=True, enter=True)
        await asyncio.sleep(_STATUS_CAPTURE_DELAY_S)
        return True

    async def collect_usage_status(
        self, session: str
    ) -> SimpleResult[HarnessUsageStatus]:
        for attempt in range(2):
            await self.request_usage_status(session)
            pane = await tmux.capture_pane(session, lines=160)
            status = parse_codex_status_pane(pane)
            if status.windows:
                return ok_result(status)
            if attempt == 0:
                await asyncio.sleep(_STATUS_RETRY_DELAY_S)
        return fail_result("codex /status did not expose any usage windows")
