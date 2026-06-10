"""Antigravity CLI adapter (`agy --dangerously-skip-permissions`).

Pane regexes were checked against `agy 1.0.2` on 2026-05-28 using the
recordings under ``tools/testing/recordings/20260527-21*-agy-*``.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import ClassVar

from murder.llm.harnesses.base import HarnessAdapter
from murder.llm.harnesses.models import HarnessModelState, HarnessStartSpec
from murder.llm.harnesses.parsing import (
    extract_last_message_heuristic,
    normalize_effort,
    parse_antigravity_model_choices,
    slug_model_label,
    strip_ansi,
)
from murder.llm.harnesses.results import SimpleResult, fail_result, ok_result
from murder.runtime.terminal import tmux

_TAIL_LINES = 25
_MODEL_MENU_DELAY_S = 0.5
_MODEL_SETTLE_DELAY_S = 0.6

_BANNER_RE = re.compile(r"Antigravity CLI\s+\d", re.IGNORECASE)
_SIGNING_IN_RE = re.compile(r"Signing in", re.IGNORECASE)
_IDLE_FOOTER_RE = re.compile(r"\?\s*for shortcuts", re.IGNORECASE)
_MODAL_FOOTER_RE = re.compile(r"esc to cancel", re.IGNORECASE)
_BUSY_RE = re.compile(r"Generating\.\.\.", re.IGNORECASE)
_AGY_STATUS_MODEL_RE = re.compile(
    r"(?P<label>(?:Gemini|Claude|GPT|Sonnet|Opus)[^\n]{2,60}?"
    r"\((?:Low|Medium|High|Thinking)\))",
    re.IGNORECASE,
)
_TRUST_PROMPT_RE = re.compile(
    r"Do you trust the contents|Yes, I trust this folder",
    re.IGNORECASE,
)


def _tail(pane_text: str) -> str:
    lines = pane_text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines[-_TAIL_LINES:])


def _agy_label_parts(label: str) -> tuple[str, str | None]:
    clean = re.sub(r"\s+", " ", label).strip()
    effort_match = re.search(r"\((Low|Medium|High|Thinking)\)", clean, re.IGNORECASE)
    effort = normalize_effort(effort_match.group(1)) if effort_match else None
    base = re.sub(r"\([^)]+\)", "", clean).strip()
    return slug_model_label(base), effort


class AntigravityAdapter(HarnessAdapter):
    kind: ClassVar[str] = "antigravity"
    crow_system_prompt: ClassVar[str] = "see prompts/crow_antigravity.md"
    model_list_command: ClassVar[str | None] = "/model"
    model_list_capture_delay_s: ClassVar[float] = 0.8
    supported_efforts: ClassVar[tuple[str, ...]] = ("low", "medium", "high")
    default_effort: ClassVar[str] = "medium"
    assume_default_effort_when_omitted: ClassVar[bool] = False
    available_startup_models: ClassVar[list[tuple[str, str]]] = []

    def startup_cmd(self, cwd: Path) -> list[str]:
        del cwd
        return ["agy", "--dangerously-skip-permissions"]

    def is_ready(self, pane_text: str) -> bool:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _SIGNING_IN_RE.search(tail):
            return False
        if _TRUST_PROMPT_RE.search(tail):
            return True
        return bool(
            _IDLE_FOOTER_RE.search(tail)
            or _MODAL_FOOTER_RE.search(tail)
            or _BANNER_RE.search(tail)
        )

    def is_idle(self, pane_text: str) -> bool:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _TRUST_PROMPT_RE.search(tail) or _BUSY_RE.search(tail):
            return False
        return bool(_IDLE_FOOTER_RE.search(tail))

    def is_busy(self, pane_text: str) -> bool:
        return bool(_BUSY_RE.search(_tail(strip_ansi(pane_text))))

    def extract_last_message(self, pane_text: str) -> str | None:
        return extract_last_message_heuristic(pane_text)

    def parse_active_model_state(self, pane_text: str) -> HarnessModelState | None:
        clean = strip_ansi(pane_text)
        matches = list(_AGY_STATUS_MODEL_RE.finditer(clean))
        if not matches:
            return None
        label = matches[-1].group("label").strip()
        model_id, effort = _agy_label_parts(label)
        if not model_id:
            return None
        return HarnessModelState(model=model_id, effort=effort)

    async def set_model(self, session: str, model: str, *, effort: str | None = None) -> bool:
        desired_model = (
            slug_model_label(model) if re.search(r"[ A-Z(]", model) else model.strip().lower()
        )
        desired_effort = normalize_effort(effort) if effort else None

        await tmux.send_keys(session, "/model", literal=True, enter=True)
        await asyncio.sleep(_MODEL_MENU_DELAY_S)
        pane = await tmux.capture_pane(session, lines=200)
        choices = parse_antigravity_model_choices(pane)
        if not choices:
            await tmux.send_keys(session, "Escape", literal=False, enter=False)
            return False

        def _matches(choice_label: str) -> bool:
            row_model, row_effort = _agy_label_parts(choice_label)
            if row_model != desired_model:
                return False
            if desired_effort is None:
                return True
            return row_effort == desired_effort

        target_idx = next(
            (idx for idx, choice in enumerate(choices) if _matches(choice.label)),
            None,
        )
        current_idx = next((idx for idx, choice in enumerate(choices) if choice.current), 0)
        if target_idx is None:
            await tmux.send_keys(session, "Escape", literal=False, enter=False)
            return False

        steps = target_idx - current_idx
        key = "Down" if steps > 0 else "Up"
        for _ in range(abs(steps)):
            await tmux.send_keys(session, key, literal=False, enter=False)
            await asyncio.sleep(0.08)
        await tmux.send_keys(session, "", literal=True, enter=True)
        await asyncio.sleep(_MODEL_SETTLE_DELAY_S)

        pane = await tmux.capture_pane(session, lines=200)
        state = self.parse_active_model_state(pane)
        if state is None or state.model != desired_model:
            return False
        if desired_effort is not None and state.effort != desired_effort:
            return False
        return True

    async def collect_available_models(self, session: str) -> SimpleResult[list[tuple[str, str]]]:
        requested = await self.request_model_list(session)
        if not requested:
            return fail_result(f"{self.kind} does not support /model discovery")
        pane = await tmux.capture_pane(session, lines=200)
        choices = parse_antigravity_model_choices(pane)
        if not choices:
            return fail_result(f"{self.kind} /model did not expose any model choices")
        rows: list[tuple[str, str]] = []
        seen: set[str] = set()
        for choice in choices:
            model_id, _ = _agy_label_parts(choice.label)
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            rows.append((model_id, model_id.replace("-", " ").title()))
        return ok_result(rows)

    async def initialize_defaults(self, session: str, spec: HarnessStartSpec) -> SimpleResult[None]:
        del spec
        for _ in range(15):
            try:
                pane = strip_ansi(await tmux.capture_pane(session, lines=40))
            except tmux.TmuxError:
                return ok_result()
            if _TRUST_PROMPT_RE.search(pane):
                await tmux.send_keys(session, "", literal=True, enter=True)
                await asyncio.sleep(0.6)
                return ok_result()
            if self.is_idle(pane):
                return ok_result()
            await asyncio.sleep(0.4)
        return ok_result()

    async def interrupt(self, session: str) -> None:
        await self.interrupt_generation(session)
