"""Codex CLI adapter (`codex --no-alt-screen ...`).

Pane regexes were checked against `codex v0.128.0` on 2026-05-02. The
adapter runs Codex in inline mode so tmux capture-pane can see the live UI
instead of an alternate-screen buffer.
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable, ClassVar

from murder.runtime.terminal import tmux
from murder.llm.harnesses.base import (
    HarnessAdapter,
    UsageCollectionMode,
)
from murder.llm.harnesses.models import HarnessModelState, HarnessUsageStatus
from murder.llm.harnesses.parsing import (
    extract_last_message_heuristic,
    normalize_effort,
    parse_numbered_effort_choices,
    parse_numbered_model_choices,
    strip_ansi,
)
from murder.llm.harnesses.results import SimpleResult, fail_result, ok_result
from murder.llm.harnesses.usage import parse_codex_status_pane

_log = logging.getLogger(__name__)

_TAIL_LINES = 30

_BANNER_RE = re.compile(r"OpenAI Codex", re.IGNORECASE)
# The Codex input box renders as a "› …" line; the placeholder text after it
# rotates ("Explain this codebase", "Find and fix a bug in @filename", …), so
# match any "› " line (busy state is screened separately, before this check).
_IDLE_PROMPT_RE = re.compile(r"^\s*›(?:\s.*)?$", re.MULTILINE)
_FOOTER_RE = re.compile(
    r"^\s*[A-Za-z0-9][A-Za-z0-9._:+/-]*"
    r"(?:\s+(?:low|medium|high|extra\s+high|xhigh))?\s+·\s+",
    re.IGNORECASE,
)
_BULLET_RE = re.compile(r"^•\s+", re.MULTILINE)
_COMPLETION_RE = re.compile(r"^\s*─\s*Worked\s+for\s+.+?\s*─\s*$", re.MULTILINE)
_BUSY_RE = re.compile(
    r"^\s*(?:[•·]\s*)?(?:working|thinking|running|executing|processing|applying patch)\b"
    r"|esc to interrupt",
    re.IGNORECASE | re.MULTILINE,
)
_LOGIN_RE = re.compile(r"\b(login required|not logged in|codex login)\b", re.IGNORECASE)

_STATUS_COMMAND_POPUP_DELAY_S = 0.5
_STATUS_FIRST_ENTER_DELAY_S = 0.8
_STATUS_CAPTURE_DELAY_S = 1.2
_STATUS_RETRY_DELAY_S = 0.6
_STATUS_DISMISS_DELAY_S = 0.1
_MODEL_POLL_INTERVAL_S = 0.4
_MODEL_STARTUP_POLL_TIMEOUT_S = 15.0
_MODEL_CAPTURE_DELAY_S = 3.0
_MODEL_STEP_DELAY_S = 0.6
# Pane-change polling for the model picker (replaces fixed _MODEL_STEP_DELAY_S
# waits between key injection and reading the next UI state). A fixed sleep that
# is too short on a slow machine lets the picker read a stale pane and pick the
# wrong model/effort; polling waits for the expected state up to the timeout and
# fails soft (continues) so a slow render degrades gracefully instead of locking.
_MODEL_STEP_POLL_INTERVAL_S = 0.1
_MODEL_STEP_POLL_TIMEOUT_S = 2.0
_PROMPT_SUBMIT_DELAY_S = 0.2
_PROMPT_VERIFY_DELAY_S = 0.8
_PROMPT_SUBMIT_RETRIES = 2
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


def _model_state_matches(
    state: HarnessModelState | None,
    *,
    model: str,
    effort: str | None,
) -> bool:
    if state is None or state.model != model:
        return False
    return effort is None or state.effort in (effort, None)


def _live_prompt_text(pane_text: str) -> str | None:
    lines = strip_ansi(pane_text).splitlines()
    for index in range(len(lines) - 1, -1, -1):
        match = _IDLE_PROMPT_RE.match(lines[index])
        if match is None:
            continue
        below = "\n".join(lines[index + 1 :])
        if _BULLET_RE.search(below) or _COMPLETION_RE.search(below):
            return None
        parts = [lines[index].strip()[1:].strip()]
        for line in lines[index + 1 :]:
            stripped = line.strip()
            if not stripped:
                break
            if line.lstrip().startswith(("›", "•")) or _FOOTER_RE.search(line):
                break
            parts.append(stripped)
        return " ".join(part for part in parts if part)
    return None


def _prompt_still_in_composer(pane_text: str, prompt: str) -> bool:
    expected = re.sub(r"\s+", " ", prompt).strip()
    if not expected:
        return False
    live = _live_prompt_text(pane_text)
    return live is not None and re.sub(r"\s+", " ", live).strip() == expected


class CodexAdapter(HarnessAdapter):
    kind: ClassVar[str] = "codex"
    usage_collection_mode: ClassVar[UsageCollectionMode] = "tmux_slash"
    startup_model_selects_runtime_model: ClassVar[bool] = True
    # Codex's model picker is `/model` (singular); it opens a numbered modal
    # list (`› 1. gpt-5.5 (current)  Frontier model …`). This adapter overrides
    # `request_model_list` and parses the modal with `parse_numbered_model_choices`
    # (not the base `parse_harness_model_list`). The modal needs a beat to render,
    # so capture late.
    model_list_command: ClassVar[str | None] = "/model"
    model_list_capture_delay_s: ClassVar[float] = 3.0
    supported_efforts: ClassVar[tuple[str, ...]] = ("low", "medium", "high", "xhigh")
    crow_system_prompt: ClassVar[str] = "see prompts/crow_codex.md"
    available_startup_models: ClassVar[list[tuple[str, str]]] = [
        ("gpt-5.5", "GPT-5.5"),
        ("gpt-5.4", "GPT-5.4"),
        ("gpt-5.4-mini", "GPT-5.4 Mini"),
        ("gpt-5.3-codex", "GPT-5.3 Codex"),
        ("gpt-5.2", "GPT-5.2"),
    ]

    def startup_cmd(self, cwd: Path) -> list[str]:
        del cwd
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
        for path in self.additional_workspace_dirs:
            cmd.extend(["--add-dir", str(path)])
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

    def is_input_ready(self, pane_text: str) -> bool | None:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _LOGIN_RE.search(tail) or self.is_busy(tail):
            return False
        return _live_prompt_text(clean) is not None

    def is_busy(self, pane_text: str) -> bool:
        return bool(_BUSY_RE.search(_tail(strip_ansi(pane_text))))

    def extract_last_message(self, pane_text: str) -> str | None:
        return extract_last_message_heuristic(pane_text)

    async def _submit_prompt(self, session: str) -> None:
        await tmux.send_keys(session, "Enter", literal=False, enter=False)

    async def _ensure_prompt_submitted(self, session: str, prompt: str) -> SimpleResult[None]:
        if not prompt.strip():
            return ok_result()
        for _ in range(_PROMPT_SUBMIT_RETRIES):
            await asyncio.sleep(_PROMPT_VERIFY_DELAY_S)
            pane = await tmux.capture_pane(session, lines=120)
            if self.is_busy(pane) or not _prompt_still_in_composer(pane, prompt):
                return ok_result()
            await self._submit_prompt(session)
        await asyncio.sleep(_PROMPT_VERIFY_DELAY_S)
        pane = await tmux.capture_pane(session, lines=120)
        if _prompt_still_in_composer(pane, prompt):
            return fail_result("codex prompt submit did not clear the composer")
        return ok_result()

    async def send_prompt(self, session: str, prompt: str) -> SimpleResult[None]:
        raw = prompt.encode("utf-8")
        if len(raw) < tmux.LARGE_PAYLOAD_BYTES:
            await tmux.send_keys(session, prompt, literal=True, enter=False)
            await asyncio.sleep(_PROMPT_SUBMIT_DELAY_S)
            await self._submit_prompt(session)
            return await self._ensure_prompt_submitted(session, prompt)

        for piece in _utf8_byte_chunks(raw, _CODEX_PASTE_CHUNK_UTF8):
            await tmux.paste_buffer_literal(session, piece.decode("utf-8"))
            await asyncio.sleep(_PROMPT_SUBMIT_DELAY_S)
            await tmux.send_keys(session, "Tab", literal=False, enter=False)
            await self._submit_prompt(session)
        return ok_result()

    async def _poll_pane_for(
        self,
        session: str,
        predicate: Callable[[str], bool],
        *,
        what: str,
        timeout_s: float = _MODEL_STEP_POLL_TIMEOUT_S,
    ) -> str:
        """Poll the pane until ``predicate`` holds, returning the latest capture.

        Replaces fixed post-keystroke sleeps in the model picker: a key is sent,
        then we wait for the pane to actually reach the expected state instead of
        guessing a delay. Fails soft — on timeout we log a warning and return the
        last capture so the caller proceeds (best-effort) rather than hanging.
        """
        attempts = max(1, int(timeout_s / _MODEL_STEP_POLL_INTERVAL_S))
        pane = ""
        for _ in range(attempts):
            pane = await tmux.capture_pane(session, lines=200)
            if predicate(pane):
                return pane
            await asyncio.sleep(_MODEL_STEP_POLL_INTERVAL_S)
        _log.warning("codex model picker: timed out waiting for %s; proceeding", what)
        return pane

    async def set_model(self, session: str, model: str, *, effort: str | None = None) -> bool:
        # The launch --model flag selects the model but not its reasoning effort,
        # so we cannot blanket-trust a startup model here: a non-default effort
        # still has to be driven through the picker. We only skip the picker when
        # the model+effort already read back correct (below), or as a degraded
        # fallback when the picker can't be driven.
        desired_effort = normalize_effort(effort) if effort else self.default_effort
        pane = await tmux.capture_pane(session, lines=200)
        state = self.parse_active_model_state(pane)
        if _model_state_matches(state, model=model, effort=desired_effort):
            return True

        if not await self.request_model_list(session):
            # Picker never rendered → trust the launch flag for the model
            # (effort stays best-effort in this degraded path).
            degraded = self.startup_model == model
            _log.warning(
                "codex set_model degraded: picker never rendered for model=%r effort=%r; "
                "returning %s on launch-flag match alone (model NOT pane-confirmed)",
                model,
                desired_effort,
                degraded,
            )
            return degraded
        pane = await tmux.capture_pane(session, lines=200)
        choices = parse_numbered_model_choices(pane)
        choice = next((c for c in choices if c.model_id == model), None)
        if choice is None or choice.index is None:
            await tmux.send_keys(session, "Escape", literal=False, enter=False)
            # Model absent from the rendered list → same launch-flag fallback.
            degraded = self.startup_model == model
            _log.warning(
                "codex set_model degraded: model=%r absent from rendered picker list; "
                "returning %s on launch-flag match alone (model NOT pane-confirmed)",
                model,
                degraded,
            )
            return degraded

        await tmux.send_keys(session, str(choice.index), literal=True, enter=False)

        effort_selection_available = False
        if desired_effort is not None:
            # Poll for the effort sub-menu to render rather than a fixed sleep:
            # if we read the pane before the menu paints, parse returns empty and
            # the effort is silently skipped → wrong (default) reasoning effort.
            effort_pane = await self._poll_pane_for(
                session,
                lambda p: bool(parse_numbered_effort_choices(p)),
                what="effort sub-menu",
            )
            effort_choices = parse_numbered_effort_choices(effort_pane)
            effort_selection_available = bool(effort_choices)
            effort_choice = next((c for c in effort_choices if c.effort == desired_effort), None)
            if effort_choice is not None and effort_choice.index is not None:
                await tmux.send_keys(session, str(effort_choice.index), literal=True, enter=False)
                # Wait for the committed model/effort to read back instead of a
                # blind sleep; fail-soft so the verify below still runs on timeout.
                await self._poll_pane_for(
                    session,
                    lambda p: _model_state_matches(
                        self.parse_active_model_state(p),
                        model=model,
                        effort=desired_effort,
                    ),
                    what="model+effort confirmation",
                )

        pane = await tmux.capture_pane(session, lines=200)
        state = self.parse_active_model_state(pane)
        verified_effort = desired_effort if effort_selection_available else None
        if _model_state_matches(state, model=model, effort=verified_effort):
            return True
        # Picker was driven but the change didn't read back; trust the launch
        # flag for the model rather than failing on a slow/garbled pane read.
        degraded = self.startup_model == model
        _log.warning(
            "codex set_model degraded: picker driven but model=%r effort=%r did not read "
            "back; returning %s on launch-flag match alone (state NOT pane-confirmed)",
            model,
            desired_effort,
            degraded,
        )
        return degraded

    def parse_active_model_state(self, pane_text: str) -> HarnessModelState | None:
        clean = strip_ansi(pane_text)
        patterns = (
            re.compile(
                r"\bmodel:\s*(?P<model>[A-Za-z0-9][A-Za-z0-9._:+/-]*)\s+"
                r"(?:(?P<effort>low|medium|high|extra\s+high|xhigh)\b)?",
                re.IGNORECASE,
            ),
            # Bottom-left status line "<model> <effort> · ~/cwd". The effort word
            # is REQUIRED here: without it any "<word> · <something>" footer
            # (e.g. "myproject · ~/path · 23% left") would parse as a model. The
            # effort-less status form is covered by the "model:" pattern above.
            re.compile(
                r"^\s*(?P<model>[A-Za-z0-9][A-Za-z0-9._:+/-]*)"
                r"\s+(?P<effort>low|medium|high|extra\s+high|xhigh)\s+·\s",
                re.IGNORECASE | re.MULTILINE,
            ),
            re.compile(
                r"\bModel changed to\s+(?P<model>[A-Za-z0-9][A-Za-z0-9._:+/-]*)\s+"
                r"(?:(?P<effort>low|medium|high|extra\s+high|xhigh)\b)?",
                re.IGNORECASE,
            ),
        )
        for pattern in patterns:
            matches = list(pattern.finditer(clean))
            if not matches:
                continue
            match = matches[-1]
            return HarnessModelState(
                model=match.group("model"),
                effort=normalize_effort(match.group("effort")),
            )
        return None

    async def interrupt(self, session: str) -> None:
        await self.interrupt_generation(session)

    async def request_model_list(self, session: str) -> bool:
        startup_attempts = max(1, int(_MODEL_STARTUP_POLL_TIMEOUT_S / _MODEL_POLL_INTERVAL_S))
        for _ in range(startup_attempts):
            pane = await tmux.capture_pane(session, lines=200)
            if self.is_idle(pane):
                break
            await asyncio.sleep(_MODEL_POLL_INTERVAL_S)
        else:
            return False

        await tmux.send_keys(session, "/model", literal=True, enter=False)
        await tmux.send_keys(session, "", literal=True, enter=True)

        picker_attempts = max(1, int(_MODEL_CAPTURE_DELAY_S / _MODEL_POLL_INTERVAL_S))
        for _ in range(picker_attempts):
            pane = await tmux.capture_pane(session, lines=200)
            if parse_numbered_model_choices(pane):
                return True
            await asyncio.sleep(_MODEL_POLL_INTERVAL_S)
        return False

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

    async def collect_usage_status(self, session: str) -> SimpleResult[HarnessUsageStatus]:
        for attempt in range(2):
            await self.request_usage_status(session)
            pane = await tmux.capture_pane(session, lines=160)
            status = parse_codex_status_pane(pane)
            if status.windows:
                return ok_result(status)
            if attempt == 0:
                await asyncio.sleep(_STATUS_RETRY_DELAY_S)
        return fail_result("codex /status did not expose any usage windows")
