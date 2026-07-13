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
from murder.llm.harnesses.models import HarnessModelState, HarnessUsageStatus
from murder.llm.harnesses.parsing import (
    extract_last_message_heuristic,
    normalize_effort,
    parse_harness_model_list,
    parse_pointed_model_choices,
    slug_model_label,
    strip_ansi,
)
from murder.llm.harnesses.results import SimpleResult, fail_result, ok_result

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

# Number of trailing pane lines to inspect for live-state markers. The
# cursor input frame is the last ~6 lines; 20 is generous slack so we
# still catch the spinner line above the input box.
_TAIL_LINES = 20
_COMPOSER_IDS = frozenset({"composer", "composer-2", "composer-2.5"})
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


def _cursor_model_id_from_label(label: str) -> str | None:  # noqa: PLR0911
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


class CursorAdapter(HarnessAdapter):
    kind: ClassVar[str] = "cursor"
    usage_collection_mode: ClassVar[UsageCollectionMode] = "http"
    supported_efforts: ClassVar[tuple[str, ...]] = ("slow", "fast")
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

    async def collect_usage_status(self, session: str) -> SimpleResult[HarnessUsageStatus]:
        del session
        try:
            return ok_result(await asyncio.to_thread(cursor_usage.get_usage_status))
        except Exception as exc:
            return fail_result(f"cursor usage collection failed: {exc}")
