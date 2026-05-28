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

from murder.terminal import tmux
from murder.harnesses import cursor_usage
from murder.harnesses.base import (
    HarnessAdapter,
    UsageCollectionMode,
)
from murder.harnesses.models import HarnessStartSpec, HarnessUsageStatus
from murder.harnesses.parsing import (
    extract_last_message_heuristic,
    is_rule_line,
    is_status_spinner_line,
    strip_ansi,
)
from murder.harnesses.results import SimpleResult, fail_result, ok_result

# Number of trailing pane lines to inspect for live-state markers. The
# cursor input frame is the last ~6 lines; 20 is generous slack so we
# still catch the spinner line above the input box.
_TAIL_LINES = 20
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


class CursorAdapter(HarnessAdapter):
    kind: ClassVar[str] = "cursor"
    usage_collection_mode: ClassVar[UsageCollectionMode] = "http"
    # Cursor's `/model` picker is a 25-entry filterable table of display names
    # ("Sonnet 4.6  (Thinking) 200K Medium", "Composer 2", …) with no id column
    # — generic parsing yields more noise than signal, so skip discovery and
    # rely on the curated list below.
    model_list_command: ClassVar[str | None] = None
    available_startup_models: ClassVar[list[tuple[str, str]]] = [
        ("composer", "Composer"),
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
        # `cwd` is honored by tmux.create_session; we don't need to cd here.
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

    async def set_model(self, session: str, model: str) -> bool:
        """Select Cursor's model before the first real prompt.

        Cursor documents `/model <model>` as the runtime selector. We do not
        validate the model name here because the available labels are account
        and release dependent.
        """
        await tmux.send_keys(session, f"/model {model}", literal=True, enter=True)
        await asyncio.sleep(0.4)
        return True

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
