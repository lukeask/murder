"""Pi coding-agent adapter (`pi`).

Pi's README and installed package docs were checked on 2026-05-02. Runtime
model selection uses the interactive ``/model`` picker; startup ``--model`` is
not used so ``HarnessSession`` can apply the model after the REPL is ready.
"""

from __future__ import annotations

import asyncio
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
    parse_harness_model_list,
    strip_ansi,
)
# The pi chrome predicate and reasoning-prefix regex live in the grammar module
# (core/grammars import no adapter; adapter→grammar is the allowed direction).
from murder.llm.harnesses.transcripts.grammar.pi import (
    _PI_REASONING_PREFIX_RE,
    _is_pi_chrome,
)
from murder.llm.harnesses.results import SimpleResult, fail_result, ok_result
from murder.runtime.terminal import tmux

_TAIL_LINES = 30
_MODEL_MENU_DELAY_S = 0.5
_MODEL_FILTER_DELAY_S = 0.35
_MODEL_SETTLE_DELAY_S = 0.5

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
    if len(paragraphs) < 2:
        return body
    if any(_PI_REASONING_PREFIX_RE.match(part) for part in paragraphs[:-1]):
        return paragraphs[-1]
    return body


def _tail(pane_text: str) -> str:
    lines = pane_text.splitlines()
    return "\n".join(lines[-_TAIL_LINES:])


def _pi_model_filter(model: str) -> str:
    return model.rsplit("/", maxsplit=1)[-1] if "/" in model else model


def _pi_models_match(desired: str, active: str | None) -> bool:
    if active is None:
        return False
    if desired == active:
        return True
    return _pi_model_filter(desired) == _pi_model_filter(active)


class PiAdapter(HarnessAdapter):
    kind: ClassVar[str] = "pi"
    crow_system_prompt: ClassVar[str] = "see prompts/crow_pi.md"
    model_list_command: ClassVar[str | None] = "/model"
    model_list_capture_delay_s: ClassVar[float] = 3.0
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

    async def set_model(self, session: str, model: str, *, effort: str | None = None) -> bool:
        del effort
        requested = await self.request_model_list(session)
        if not requested:
            return False
        pane = await tmux.capture_pane(session, lines=200)
        choices = parse_harness_model_list(pane)
        if not any(row_id == model for row_id, _ in choices):
            await tmux.send_keys(session, "Escape", literal=False, enter=False)
            return False

        filter_text = _pi_model_filter(model)
        await tmux.send_keys(session, filter_text, literal=True, enter=False)
        await asyncio.sleep(_MODEL_FILTER_DELAY_S)
        await tmux.send_keys(session, "", literal=True, enter=True)
        await asyncio.sleep(_MODEL_SETTLE_DELAY_S)

        pane = await tmux.capture_pane(session, lines=200)
        state = self.parse_active_model_state(pane)
        return _pi_models_match(model, state.model if state else None)

    async def collect_available_models(self, session: str) -> SimpleResult[list[tuple[str, str]]]:
        requested = await self.request_model_list(session)
        if not requested:
            return fail_result(f"{self.kind} does not support /model discovery")
        pane = await tmux.capture_pane(session, lines=200)
        models = parse_harness_model_list(pane)
        if not models:
            return fail_result(f"{self.kind} /model did not expose any model choices")
        return ok_result(models)

    async def interrupt(self, session: str) -> None:
        await self.interrupt_generation(session)
