"""Cursor agent CLI adapter (`agent --yolo`).

Pane regexes were validated empirically against
`agent v2026.04.30-4edb302` on 2026-05-01. Captured fixtures live in
`tests/fixtures/harness_panes/`.

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

from murder.llm.harnesses import cursor_usage
from murder.llm.harnesses.base import (
    HarnessAdapter,
    UsageCollectionMode,
)
from murder.llm.harnesses.models import HarnessModelState, HarnessStartSpec, HarnessUsageStatus
from murder.llm.harnesses.parsing import (
    extract_last_message_heuristic,
    normalize_effort,
    parse_cursor_model_list,
    parse_cursor_model_page,
    parse_harness_model_list,
    parse_pointed_model_choices,
    slug_model_label,
    strip_ansi,
)
# The cursor chrome predicate and the regexes it owns live in the grammar module
# (core/grammars import no adapter; adapter→grammar is the allowed direction).
# Some of those regexes double as live-state markers, so import them back here.
from murder.llm.harnesses.transcripts.grammar.cursor import (
    _BUSY_INPUT_HINT_RE,
    _BUSY_SPINNER_RE,
    _CHROME_MARK,
    _CURSOR_COMPOSER_RE,
    _USER_MARK,
    _is_cursor_chrome,
)
from murder.llm.harnesses.results import SimpleResult, fail_result, ok_result
from murder.runtime.terminal import tmux

# Number of trailing pane lines to inspect for live-state markers. The
# cursor input frame is the last ~6 lines; 20 is generous slack so we
# still catch the spinner line above the input box.
_TAIL_LINES = 20
_MODEL_SETTLE_DELAY_S = 0.5
_MODEL_MENU_DELAY_S = 0.4
_MODEL_PAGE_SCROLL_DELAY_S = 0.25
_MAX_CURSOR_MODEL_PAGES = 32
_MODEL_VERIFY_ATTEMPTS = 4
_COMPOSER_IDS = frozenset({"composer", "composer-2", "composer-2.5"})
_CURSOR_SPEEDS = ("slow", "fast")
_IDLE_PLACEHOLDER_RE = re.compile(
    r"(Add a follow-up|Plan,\s*search,\s*build anything)",
    re.IGNORECASE,
)
_TRUST_PROMPT_RE = re.compile(r"Workspace Trust Required", re.IGNORECASE)
_CURSOR_SPEED_IN_LINE_RE = re.compile(r"\b(Slow|Fast)\b", re.IGNORECASE)
_CURSOR_COMPOSER_EDIT_RE = re.compile(
    r"composer\s+2(?:\.5)?\s+[—-]\s*edit\s+parameters",
    re.IGNORECASE,
)
_CURSOR_FAST_CHECKBOX_RE = re.compile(
    r"\[\s*(?P<mark>[xX✓]?)\s*\]\s*Fast\b",
    re.IGNORECASE,
)
_CURSOR_INPUT_RE = re.compile(r"^\s*→\s*\S", re.IGNORECASE | re.MULTILINE)


# The transcript grammar's preprocess_frame prefixes input-box / user-input
# lines with these control-char marks. They survive strip_ansi, and a marked
# input line (e.g. `\x02  → <restored prompt>` after an interrupt-restart) no
# longer matches the `^\s*→` input-box anchor — which silently froze is_idle at
# "not idle" and latched the conversation live_state at "working". State
# detection must see through the marks, so strip them off the tail.
_PREPROCESS_MARKS = str.maketrans("", "", _USER_MARK + _CHROME_MARK)


def _tail(pane_text: str) -> str:
    lines = pane_text.translate(_PREPROCESS_MARKS).splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines[-_TAIL_LINES:])


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
    if re.search(r"plan,\s*search|add a follow-up", lowered):
        return None
    if "composer 2.5" in lowered:
        return "composer-2.5"
    if re.search(r"\bcomposer 2\b", lowered):
        return "composer-2"
    if lowered.startswith("auto"):
        return "auto"
    if " " in label.strip():
        slug = slug_model_label(label)
        return slug.lower() if slug else None
    parsed = parse_harness_model_list(label)
    if parsed:
        return parsed[0][0].lower()
    slug = slug_model_label(label)
    return slug.lower() if slug else None


def _is_composer_model(model: str) -> bool:
    return _normalize_cursor_model(model) == "composer-2.5"


class CursorAdapter(HarnessAdapter):
    kind: ClassVar[str] = "cursor"
    usage_collection_mode: ClassVar[UsageCollectionMode] = "http"
    # Cursor's `/model` picker paginates (~10 rows per page); discovery scrolls
    # with PageDown until the footer shows the last range. Composer 2.5 also
    # supports slow/fast (Tab). ``available_startup_models`` is a fallback only.
    model_list_command: ClassVar[str | None] = "/model"
    model_list_capture_delay_s: ClassVar[float] = _MODEL_MENU_DELAY_S
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

    # Default binary name for the Cursor agent CLI. Overridable via the
    # ``binary`` field of the harness config (plumbed through HarnessStartSpec
    # onto self.binary) for installs that expose a differently-named executable.
    default_binary: ClassVar[str] = "agent"

    def startup_cmd(self, cwd: Path) -> list[str]:
        del cwd
        return [self.binary or self.default_binary, "--yolo"]

    def startup_model_satisfies_runtime_request(
        self,
        model: str,
        effort: str | None = None,
        *,
        launched_model: str | None = None,
    ) -> bool:
        normalized_model = _normalize_cursor_model(model)
        normalized_startup = _normalize_cursor_model(launched_model or self.startup_model or "")
        normalized_effort = normalize_effort(effort) if effort else None
        if (
            normalized_model == "composer-2.5"
            and normalized_startup == "composer-2.5"
            and (normalized_effort is None or normalized_effort == self.default_effort)
        ):
            return True
        return super().startup_model_satisfies_runtime_request(
            model,
            effort,
            launched_model=launched_model,
        )

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
        return bool(_IDLE_PLACEHOLDER_RE.search(tail) or _CURSOR_INPUT_RE.search(tail))

    def is_idle(self, pane_text: str) -> bool:
        """True iff input box shows a placeholder AND no busy marker is live."""
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _BUSY_INPUT_HINT_RE.search(tail) or _BUSY_SPINNER_RE.search(tail):
            return False
        return bool(_IDLE_PLACEHOLDER_RE.search(tail) or _CURSOR_INPUT_RE.search(tail))

    def is_busy(self, pane_text: str) -> bool:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        return bool(_BUSY_INPUT_HINT_RE.search(tail) or _BUSY_SPINNER_RE.search(tail))

    def extract_last_message(self, pane_text: str) -> str | None:
        return extract_last_message_heuristic(_strip_cursor_chrome(pane_text))

    def _parse_composer_speed(self, pane_text: str) -> str | None:
        clean = strip_ansi(pane_text)
        in_edit_parameters = False
        for line in clean.splitlines():
            if "composer 2.5" not in line.lower():
                if not in_edit_parameters:
                    continue
                checkbox = _CURSOR_FAST_CHECKBOX_RE.search(line)
                if checkbox:
                    return "fast" if checkbox.group("mark") else "slow"
                continue
            match = _CURSOR_SPEED_IN_LINE_RE.search(line)
            if match:
                return normalize_effort(match.group(1))
            if _CURSOR_COMPOSER_EDIT_RE.search(line):
                in_edit_parameters = True
        return None

    def _is_composer_edit_parameters(self, pane_text: str) -> bool:
        return bool(_CURSOR_COMPOSER_EDIT_RE.search(strip_ansi(pane_text)))

    def parse_active_model_state(self, pane_text: str) -> HarnessModelState | None:
        clean = strip_ansi(pane_text)
        model: str | None = None
        effort: str | None = None

        for line in _tail(clean).splitlines():
            if not _CURSOR_COMPOSER_RE.match(line.strip()):
                continue
            label = line.strip()
            # Drop the right-side auto-run mode label ("Auto-run" on older
            # CLIs, "Run Everything" on ≥ 2026.06.11) before parsing the model.
            model = _cursor_model_id_from_label(
                re.split(r"Auto-run|Run\s+Everything", label, maxsplit=1)[0]
            )
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

    async def _type_picker_filter(self, session: str, text: str) -> None:
        """Type ``text`` into the `/model` picker's filter one key at a time.

        The picker (CLI ≥ 2026.06.11) drops bulk literal input as a paste
        guard — a one-shot ``send_keys`` leaves the filter empty and a later
        Enter selects whatever row the cursor is on (Auto). Per-key sends with
        a small gap register as typing.
        """
        for ch in text:
            await tmux.send_keys(session, ch, literal=True, enter=False)
            await asyncio.sleep(0.06)

    async def _set_composer_speed(self, session: str, desired_speed: str) -> bool:
        # Picker flow (CLI ≥ 2026.06.11): filter to Composer 2.5, Tab opens its
        # "Edit Parameters" dialog with a single `[x] Fast` checkbox (slow =
        # unchecked; there is no named Slow row), Enter toggles it, Escape
        # returns to the picker, Enter commits the model.
        if desired_speed not in _CURSOR_SPEEDS:
            return False
        await tmux.send_keys(session, "/model", literal=True, enter=True)
        await asyncio.sleep(_MODEL_MENU_DELAY_S)
        await self._type_picker_filter(session, "Composer 2.5")
        await asyncio.sleep(0.25)
        await tmux.send_keys(session, "Tab", literal=False, enter=False)
        await asyncio.sleep(0.25)
        pane = await tmux.capture_pane(session, lines=200)
        if self._is_composer_edit_parameters(pane):
            current = self._parse_composer_speed(pane)
            if current != desired_speed:
                await tmux.send_keys(session, "Enter", literal=False, enter=False)
                await asyncio.sleep(0.2)
            await tmux.send_keys(session, "Escape", literal=False, enter=False)
            await asyncio.sleep(0.2)
        await tmux.send_keys(session, "", literal=True, enter=True)
        for _ in range(_MODEL_VERIFY_ATTEMPTS):
            await asyncio.sleep(_MODEL_SETTLE_DELAY_S)
            pane = await tmux.capture_pane(session, lines=200)
            state = self.parse_active_model_state(pane)
            if state is None or state.model != "composer-2.5":
                continue
            if state.effort == desired_speed:
                return True
            if desired_speed == self.default_effort and state.effort is None:
                return True
        return False

    async def set_model(self, session: str, model: str, *, effort: str | None = None) -> bool:
        desired_model = _normalize_cursor_model(model)
        desired_speed = normalize_effort(effort) if effort else None
        # slow/fast is a Composer-2.5-only control (its Tab "Edit Parameters"
        # Fast checkbox). Every other cursor model ("auto", gpt-*, sonnet-*,
        # opus-*, …) exposes no speed picker, so an effort defaulted/forwarded
        # for them is unsatisfiable — drop it rather than fail the spawn trying
        # to verify a speed the model can never report.
        if not _is_composer_model(desired_model):
            desired_speed = None
        current_state: HarnessModelState | None = None
        for attempt in range(_MODEL_VERIFY_ATTEMPTS):
            pane = await tmux.capture_pane(session, lines=200)
            current_state = self.parse_active_model_state(pane)
            current_model = _normalize_cursor_model(current_state.model or "") if current_state else None
            if current_model == desired_model:
                if desired_speed is None:
                    return True
                if current_state and current_state.effort == desired_speed:
                    return True
                if _is_composer_model(desired_model) and desired_speed == self.default_effort:
                    if current_state.effort is None:
                        return True
            if current_state is not None or attempt == _MODEL_VERIFY_ATTEMPTS - 1:
                break
            await asyncio.sleep(0.2)

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
        requested = await self.request_model_list(session)
        if not requested:
            return fail_result(f"{self.kind} does not support /model discovery")

        discovered: dict[str, str] = {}
        last_page: tuple[int, int, int] | None = None
        stagnant_pages = 0
        try:
            for _ in range(_MAX_CURSOR_MODEL_PAGES):
                pane = await tmux.capture_pane(session, lines=200)
                for model_id, label in parse_cursor_model_list(
                    pane, model_id_for_label=_cursor_model_id_from_label
                ):
                    discovered[model_id] = label

                page = parse_cursor_model_page(pane)
                if page is None:
                    if discovered:
                        break
                    continue

                _start, end, total = page
                if end >= total:
                    break
                if page == last_page:
                    stagnant_pages += 1
                    if stagnant_pages >= 2:
                        break
                else:
                    stagnant_pages = 0
                last_page = page

                await tmux.send_keys(session, "PageDown", literal=False, enter=False)
                await asyncio.sleep(_MODEL_PAGE_SCROLL_DELAY_S)
        finally:
            await tmux.send_keys(session, "Escape", literal=False, enter=False)
            await asyncio.sleep(0.15)

        if not discovered:
            if self.available_startup_models:
                return ok_result(list(self.available_startup_models))
            return fail_result(f"{self.kind} /model did not expose any model choices")

        rows = sorted(discovered.items(), key=lambda item: item[1].lower())
        return ok_result(rows)

    async def initialize_defaults(self, session: str, spec: HarnessStartSpec) -> SimpleResult[None]:
        if spec.auto_run is None:
            return ok_result()
        mode = "on" if spec.auto_run else "off"
        await tmux.send_keys(session, f"/auto-run {mode}", literal=True, enter=True)
        await asyncio.sleep(0.2)
        return ok_result()

    async def send_prompt(self, session: str, prompt: str) -> SimpleResult[None]:
        await tmux.send_keys(session, "C-u", literal=False, enter=False)
        await asyncio.sleep(0.05)
        await tmux.send_keys(session, prompt, literal=True, enter=True)
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
