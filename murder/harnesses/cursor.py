"""Cursor agent CLI adapter (`agent --yolo`).

Pane regexes were validated empirically against
`agent v2026.04.30-4edb302` on 2026-05-01. Captured fixtures live in
`tests/fixtures/cursor_panes/`.

Markers we rely on, all visible in the bottom rendered frame:

| State            | Marker                                               |
|------------------|------------------------------------------------------|
| busy             | "ctrl+c to stop" (right-aligned in input box)        |
| busy (extra)     | "Composing" / "Running" line with braille spinner    |
| idle (post-turn) | "Add a follow-up" placeholder, no busy marker        |
| idle (pre-turn)  | "Plan, search, build anything" placeholder           |
| ready/booted     | either idle marker present                           |

We restrict busy detection to the tail of the pane so historical
"ctrl+c to stop" frames left in scrollback don't mis-flag a now-idle
agent.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import ClassVar

from murder.harnesses import cursor_usage
from murder.harnesses.base import (
    HarnessAdapter,
    UsageCollectionMode,
)
from murder.harnesses.models import HarnessModelState, HarnessStartSpec, HarnessUsageStatus
from murder.harnesses.parsing import (
    extract_last_message_heuristic,
    is_rule_line,
    is_status_spinner_line,
    normalize_effort,
    parse_harness_model_list,
    parse_pointed_model_choices,
    strip_ansi,
)
from murder.harnesses.results import SimpleResult, fail_result, ok_result
from murder.terminal import tmux

# Number of trailing pane lines to inspect for live-state markers. The
# cursor input frame is the last ~6 lines; 20 is generous slack so we
# still catch the spinner line above the input box.
_TAIL_LINES = 20
_MODEL_SETTLE_DELAY_S = 0.5
_MODEL_MENU_DELAY_S = 0.4
_COMPOSER_IDS = frozenset({"composer", "composer-2", "composer-2.5"})
_CURSOR_SPEEDS = ("slow", "fast")
_IDLE_PLACEHOLDER_RE = re.compile(
    r"(Add a follow-up|Plan,\s*search,\s*build anything)",
    re.IGNORECASE,
)
_BUSY_INPUT_HINT_RE = re.compile(r"ctrl\+c to stop", re.IGNORECASE)
_BUSY_SPINNER_RE = re.compile(
    r"^\s*\S+\s+(Composing|Running|Generating|Thinking)\b",
    re.MULTILINE,
)
_TRUST_PROMPT_RE = re.compile(r"Workspace Trust Required", re.IGNORECASE)
_CURSOR_CWD_RE = re.compile(r"^\s*(?:~/|/|\./|\.\./).*\s+·\s+\S+\s*$")
_CURSOR_COMPOSER_RE = re.compile(r"^\s*Composer\b.*\bAuto-run\b", re.IGNORECASE)
_CURSOR_SPEED_IN_LINE_RE = re.compile(r"\b(Slow|Fast)\b", re.IGNORECASE)
_CURSOR_PLACEHOLDER_RE = re.compile(
    r"^\s*→\s*(?:Add a follow-up|Plan,\s*search,\s*build anything)\b",
    re.IGNORECASE,
)
_CURSOR_CHROME_RE = re.compile(
    r"""
    ^\s*(?:
        Cursor\s+Agent
        |v\d{4}\.\d{2}\.\d{2}-[A-Za-z0-9]+
        |⚠\s*Workspace\s+Trust\s+Required
        |Cursor\s+Agent\s+can\s+execute\s+code\b
        |Do\s+you\s+trust\s+the\s+contents\b
        |\[[aq]\]\s+
        |⏳\s*Trusting\s+workspace
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _tail(pane_text: str) -> str:
    lines = pane_text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines[-_TAIL_LINES:])


def _is_cursor_chrome(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if is_rule_line(line) or is_status_spinner_line(line):
        return True
    return bool(
        _CURSOR_PLACEHOLDER_RE.match(s)
        or _CURSOR_COMPOSER_RE.match(s)
        or _CURSOR_CWD_RE.match(s)
        or _BUSY_INPUT_HINT_RE.search(s)
        or _BUSY_SPINNER_RE.match(s)
        or _CURSOR_CHROME_RE.match(s)
    )


def _strip_cursor_chrome(pane_text: str) -> str:
    return "\n".join(
        line for line in strip_ansi(pane_text).splitlines() if not _is_cursor_chrome(line)
    )


def _normalize_cursor_model(model: str) -> str:
    key = model.strip().lower().replace("_", " ")
    key = re.sub(r"\s+", "-", key)
    if key in _COMPOSER_IDS or key == "composer":
        return "composer-2.5"
    return key


def _cursor_model_id_from_label(label: str) -> str | None:
    lowered = label.lower()
    if "composer 2.5" in lowered:
        return "composer-2.5"
    if re.search(r"\bcomposer 2\b", lowered):
        return "composer-2"
    if lowered.startswith("auto"):
        return "auto"
    parsed = parse_harness_model_list(label)
    if parsed:
        return parsed[0][0]
    return None


def _is_composer_model(model: str) -> bool:
    return _normalize_cursor_model(model) == "composer-2.5"


class CursorAdapter(HarnessAdapter):
    kind: ClassVar[str] = "cursor"
    usage_collection_mode: ClassVar[UsageCollectionMode] = "http"
    # Cursor's `/model` picker is a filterable table of display names. Runtime
    # ids are curated here; Composer 2.5 additionally supports slow/fast (Tab).
    model_list_command: ClassVar[str | None] = None
    supported_efforts: ClassVar[tuple[str, ...]] = _CURSOR_SPEEDS
    default_effort: ClassVar[str] = "slow"
    available_startup_models: ClassVar[list[tuple[str, str]]] = [
        ("composer-2.5", "Composer 2.5"),
        ("auto", "Auto"),
        ("gpt-5.5", "GPT-5.5"),
        ("gpt-5.4", "GPT-5.4"),
        ("claude-sonnet-4.5", "Claude Sonnet 4.5"),
    ]

    crow_system_prompt: ClassVar[str] = (
        # Loaded from prompts/crow_cursor.md at runtime by Crow.start().
        # This class attribute is just a marker; runner pulls the file.
        "see prompts/crow_cursor.md"
    )

    def startup_cmd(self, cwd: Path) -> list[str]:
        del cwd
        return ["agent", "--yolo"]

    def is_ready(self, pane_text: str) -> bool:
        """True once the input box is accepting text (cursor has booted past
        any trust/login prompts).

        Trust check is scoped to the live tail because once accepted, the
        trust dialog scrolls into history but cursor is fully usable.
        """
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _TRUST_PROMPT_RE.search(tail):
            return False
        return bool(_IDLE_PLACEHOLDER_RE.search(tail))

    def is_idle(self, pane_text: str) -> bool:
        """True iff input box shows a placeholder AND no busy marker is live."""
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _BUSY_INPUT_HINT_RE.search(tail) or _BUSY_SPINNER_RE.search(tail):
            return False
        return bool(_IDLE_PLACEHOLDER_RE.search(tail))

    def is_busy(self, pane_text: str) -> bool:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        return bool(_BUSY_INPUT_HINT_RE.search(tail) or _BUSY_SPINNER_RE.search(tail))

    def extract_last_message(self, pane_text: str) -> str | None:
        return extract_last_message_heuristic(_strip_cursor_chrome(pane_text))

    def _parse_composer_speed(self, pane_text: str) -> str | None:
        clean = strip_ansi(pane_text)
        for line in clean.splitlines():
            if "composer 2.5" not in line.lower():
                continue
            match = _CURSOR_SPEED_IN_LINE_RE.search(line)
            if match:
                return normalize_effort(match.group(1))
        return None

    def parse_active_model_state(self, pane_text: str) -> HarnessModelState | None:
        clean = strip_ansi(pane_text)
        model: str | None = None
        effort: str | None = None

        for line in _tail(clean).splitlines():
            if not _CURSOR_COMPOSER_RE.match(line.strip()):
                continue
            label = line.strip()
            model = _cursor_model_id_from_label(label.split("Auto-run", maxsplit=1)[0])
            speed_match = _CURSOR_SPEED_IN_LINE_RE.search(label)
            if speed_match:
                effort = normalize_effort(speed_match.group(1))
            break

        menu = parse_pointed_model_choices(clean, model_id_for_label=_cursor_model_id_from_label)
        current = next((choice for choice in menu if choice.current), None)
        if current is not None:
            model = current.model_id

        speed = self._parse_composer_speed(clean)
        if speed is not None:
            effort = speed

        if model is None and effort is None:
            return None
        return HarnessModelState(model=model, effort=effort)

    async def _set_composer_speed(self, session: str, desired_speed: str) -> bool:
        if desired_speed not in _CURSOR_SPEEDS:
            return False
        await tmux.send_keys(session, "/model", literal=True, enter=True)
        await asyncio.sleep(_MODEL_MENU_DELAY_S)
        await tmux.send_keys(session, "Composer 2.5", literal=True, enter=False)
        await asyncio.sleep(0.25)
        pane = await tmux.capture_pane(session, lines=200)
        current = self._parse_composer_speed(pane)
        for _ in range(len(_CURSOR_SPEEDS) + 1):
            if current == desired_speed:
                break
            await tmux.send_keys(session, "Tab", literal=False, enter=False)
            await asyncio.sleep(0.12)
            pane = await tmux.capture_pane(session, lines=200)
            current = self._parse_composer_speed(pane)
        await tmux.send_keys(session, "", literal=True, enter=True)
        await asyncio.sleep(_MODEL_SETTLE_DELAY_S)
        pane = await tmux.capture_pane(session, lines=200)
        state = self.parse_active_model_state(pane)
        if state is None or state.model != "composer-2.5":
            return False
        return state.effort == desired_speed

    async def set_model(self, session: str, model: str, *, effort: str | None = None) -> bool:
        desired_model = _normalize_cursor_model(model)
        desired_speed = normalize_effort(effort) if effort else None

        if _is_composer_model(desired_model) and desired_speed is not None:
            return await self._set_composer_speed(session, desired_speed)

        await tmux.send_keys(session, f"/model {model}", literal=True, enter=True)
        await asyncio.sleep(_MODEL_SETTLE_DELAY_S)
        pane = await tmux.capture_pane(session, lines=200)
        state = self.parse_active_model_state(pane)
        if state is None:
            curated = {_normalize_cursor_model(m) for m, _ in self.available_startup_models}
            return desired_model in curated
        active_model = _normalize_cursor_model(state.model or "")
        if active_model != desired_model:
            return False
        if desired_speed is not None and state.effort != desired_speed:
            return False
        return True

    async def collect_available_models(self, session: str) -> SimpleResult[list[tuple[str, str]]]:
        del session
        if self.available_startup_models:
            return ok_result(list(self.available_startup_models))
        return fail_result(f"{self.kind} has no curated startup models")

    async def initialize_defaults(self, session: str, spec: HarnessStartSpec) -> SimpleResult[None]:
        mode = "on" if spec.auto_run is not False else "off"
        await tmux.send_keys(session, f"/auto-run {mode}", literal=True, enter=True)
        await asyncio.sleep(0.2)
        return ok_result()

    async def interrupt(self, session: str) -> None:
        await self.interrupt_generation(session)

    async def request_usage_status(self, session: str) -> bool:
        del session
        return True

    async def collect_usage_status(self, session: str) -> SimpleResult[HarnessUsageStatus]:
        del session
        try:
            return ok_result(await asyncio.to_thread(cursor_usage.get_usage_status))
        except Exception as exc:
            return fail_result(f"cursor usage collection failed: {exc}")
