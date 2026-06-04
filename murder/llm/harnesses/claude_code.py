"""Claude Code CLI adapter (`claude --dangerously-skip-permissions`).

Caveats:
- `--dangerously-skip-permissions` refuses to run as root.
- CC's UI has tool-box rendering and spinners; pane regexes need
  empirical tuning during M1/M2.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import ClassVar

from murder.runtime.terminal import tmux
from murder.llm.harnesses.base import (
    HarnessAdapter,
    UsageCollectionMode,
)
from murder.llm.harnesses.models import HarnessModelState, HarnessUsageStatus
from murder.llm.harnesses.parsing import (
    extract_last_message_heuristic,
    normalize_effort,
    parse_claude_code_model_choices,
    strip_ansi,
)
from murder.llm.harnesses.results import SimpleResult, fail_result, ok_result
from murder.llm.harnesses.usage import parse_claude_usage_pane

_MODEL_CAPTURE_DELAY_S = 0.6
_MODEL_MENU_DELAY_S = 0.4
_CC_EFFORT_ORDER = ("low", "medium", "high", "xhigh", "max")
_CC_MODEL_LINE_RE = re.compile(
    r"\b(?P<model>Opus|Sonnet|Haiku)\b(?:\s+\d+(?:\.\d+)*)?.*?"
    r"\bwith\s+(?P<effort>low|medium|high|x\s*high|xhigh|max)\s+effort\b",
    re.IGNORECASE,
)
# Fallback: banner line "Haiku 4.5 · Claude Pro" without effort text. The
# trailing " ·" anchors this to the banner/status line so a bare "Opus 4.x"
# mentioned in conversation prose can't be misread as the active model.
_CC_BANNER_MODEL_RE = re.compile(
    r"\b(?P<model>Opus|Sonnet|Haiku)\s+\d+\.\d+\s+·",
    re.IGNORECASE,
)
_CC_EFFORT_STATUS_RE = re.compile(
    r"[●•]\s*(?P<effort>low|medium|high|x\s*high|xhigh|max)\s*(?:·|$)",
    re.IGNORECASE,
)
_CC_MENU_EFFORT_RE = re.compile(
    r"[●•]\s*(?P<effort>low|medium|high|x\s*high|xhigh|max)\s+effort\b",
    re.IGNORECASE,
)


def _claude_model_id(model: str | None) -> str | None:
    if model is None:
        return None
    lowered = model.strip().lower()
    if not lowered:
        return None
    if "opus" in lowered:
        return "opus"
    if "sonnet" in lowered:
        return "sonnet"
    if "haiku" in lowered:
        return "haiku"
    return None


def _shortest_effort_keys(current: str, desired: str) -> list[str]:
    if current == desired:
        return []
    cur = _CC_EFFORT_ORDER.index(current)
    target = _CC_EFFORT_ORDER.index(desired)
    right = (target - cur) % len(_CC_EFFORT_ORDER)
    left = (cur - target) % len(_CC_EFFORT_ORDER)
    if left <= right:
        return ["Left"] * left
    return ["Right"] * right

class ClaudeCodeAdapter(HarnessAdapter):
    kind: ClassVar[str] = "claude_code"
    usage_collection_mode: ClassVar[UsageCollectionMode] = "tmux_slash"
    # CC's `/model` opens a radio dialog of human labels. Adapter-specific
    # parsing maps those labels to slash-command ids (`opus`, `sonnet`, `haiku`).
    model_list_command: ClassVar[str | None] = "/model"
    model_list_capture_delay_s: ClassVar[float] = _MODEL_CAPTURE_DELAY_S
    supported_efforts: ClassVar[tuple[str, ...]] = _CC_EFFORT_ORDER
    # Transcript parsing. CC echoes the submitted prompt on a "❯ …" line; the
    # reply (●) and tool boxes (⏺ ⎿) follow until the next prompt.  Status-bar
    # and "✻ …" spinner lines are dropped; the empty trailing "❯ " and the
    # pre-first-message placeholder both parse to no turn.
    # Only ❯ (U+276F) is used — plain ">" appears in diffs/blockquotes inside
    # assistant output and must not be treated as a turn boundary.
    transcript_prompt_markers: ClassVar[tuple[str, ...]] = ("❯",)
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
    _BUSY_RE = re.compile(r"(?:thinking|Esc to interrupt)", re.IGNORECASE)
    _CC2_UI_RE = re.compile(r"bypass permissions", re.IGNORECASE)
    # CC 2.x: "Esc to interrupt" appears in status bar ONLY while actively generating.
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
        del cwd
        cmd = ["claude", "--dangerously-skip-permissions"]
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

    async def set_model(self, session: str, model: str, *, effort: str | None = None) -> bool:
        desired_model = _claude_model_id(model)
        if desired_model is None:
            return False
        desired_effort = normalize_effort(effort) if effort else self.default_effort

        await tmux.send_keys(session, f"/model {desired_model}", literal=True, enter=True)
        await asyncio.sleep(_MODEL_CAPTURE_DELAY_S)

        if desired_effort is not None:
            await self._set_effort(session, desired_effort)

        pane = await tmux.capture_pane(session, lines=200)
        state = self.parse_active_model_state(pane)
        if state is None or state.model != desired_model:
            return False
        # Only reject on effort mismatch when effort is readable; if the pane
        # doesn't show effort text (older CC, or freshly-switched), trust the
        # model match and the set_effort call.
        if state.effort is not None and desired_effort is not None and state.effort != desired_effort:
            return False
        return True

    async def _set_effort(self, session: str, desired_effort: str) -> None:
        await tmux.send_keys(session, "/model", literal=True, enter=True)
        await asyncio.sleep(_MODEL_MENU_DELAY_S)
        pane = await tmux.capture_pane(session, lines=200)
        current = self.parse_active_model_state(pane)
        current_effort = current.effort if current else None
        if current_effort in _CC_EFFORT_ORDER and current_effort != desired_effort:
            for key in _shortest_effort_keys(current_effort, desired_effort):
                await tmux.send_keys(session, key, literal=False, enter=False)
                await asyncio.sleep(0.05)
        await tmux.send_keys(session, "", literal=True, enter=True)
        await asyncio.sleep(_MODEL_MENU_DELAY_S)

    def parse_active_model_state(self, pane_text: str) -> HarnessModelState | None:
        clean = strip_ansi(pane_text)
        model: str | None = None
        effort: str | None = None

        matches = list(_CC_MODEL_LINE_RE.finditer(clean))
        if matches:
            match = matches[-1]
            model = _claude_model_id(match.group("model"))
            effort = normalize_effort(match.group("effort"))

        if model is None:
            # Older CC (v2.1.150) or freshly-switched model: banner shows
            # "Haiku 4.5 · Claude Pro" without effort text.
            banner_match = _CC_BANNER_MODEL_RE.search(clean)
            if banner_match:
                model = _claude_model_id(banner_match.group("model"))

        menu_choices = parse_claude_code_model_choices(clean)
        current_choice = next((choice for choice in menu_choices if choice.current), None)
        if current_choice is not None:
            model = current_choice.model_id

        effort_matches = list(_CC_MENU_EFFORT_RE.finditer(clean))
        if effort_matches:
            effort = normalize_effort(effort_matches[-1].group("effort"))
        elif effort is None:
            status_matches = list(_CC_EFFORT_STATUS_RE.finditer(clean))
            if status_matches:
                effort = normalize_effort(status_matches[-1].group("effort"))

        if model is None and effort is None:
            return None
        return HarnessModelState(model=model, effort=effort)

    async def collect_available_models(self, session: str) -> SimpleResult[list[tuple[str, str]]]:
        requested = await self.request_model_list(session)
        if not requested:
            return fail_result(f"{self.kind} does not support /models discovery")
        pane = await tmux.capture_pane(session, lines=200)
        models = [(choice.model_id, choice.label) for choice in parse_claude_code_model_choices(pane)]
        if not models:
            return fail_result(f"{self.kind} /model did not expose any model choices")
        return ok_result(models)

    async def interrupt(self, session: str) -> None:
        await self.interrupt_generation(session)

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
