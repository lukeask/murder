"""Antigravity CLI adapter (`agy --dangerously-skip-permissions`).

Pane regexes were checked against `agy 1.0.2` on 2026-05-28 using the
recordings under ``tools/testing/recordings/20260527-21*-agy-*``.

Markers we rely on (last ~25 lines of the pane):

| State            | Marker                                                 |
|------------------|--------------------------------------------------------|
| idle (REPL)      | "? for shortcuts" footer line                          |
| busy             | "Generating..." spinner line                           |
| modal open       | "esc to cancel" footer (also shown during generation)  |
| trust dialog     | "Do you trust the contents …" / "I trust this folder"  |

Two notable differences from the CC/Codex pattern:

* ``agy`` has no ``--model`` flag (``agy --help`` lists none), so
  ``startup_model`` cannot be honoured at process launch. ``/model`` opens
  a numbered picker of human labels with no machine-readable ids, so
  runtime model selection is also disabled — like Cursor and Claude Code,
  ``set_model`` only succeeds when the desired model already matches what
  the user picked outside the harness.
* Usage tracking is not exposed via a slash command in 1.0.2; gauges
  (``schedule_snapshot.py`` / ``tui/dispatch/gauges.py``) skip the
  ``antigravity`` harness intentionally — add it there when a usage
  surface lands.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import ClassVar

from murder.harnesses.base import HarnessAdapter
from murder.harnesses.models import HarnessStartSpec
from murder.harnesses.parsing import (
    extract_last_message_heuristic,
    strip_ansi,
)
from murder.harnesses.results import SimpleResult, ok_result
from murder.terminal import tmux

# The Antigravity UI is ~25 lines tall; the footer sits on the very last
# rendered line, so 25 is the smallest tail that always captures it.
_TAIL_LINES = 25

# Post-login header includes a version string ("Antigravity CLI 1.0.2");
# the pre-login splash uses "Welcome to the Antigravity CLI" + a "Signing
# in..." spinner. Matching only the versioned form avoids a false-positive
# during sign-in.
_BANNER_RE = re.compile(r"Antigravity CLI\s+\d", re.IGNORECASE)
_SIGNING_IN_RE = re.compile(r"Signing in", re.IGNORECASE)

# Footer text on the very bottom rendered line. "? for shortcuts" is the
# REPL/idle hint; "esc to cancel" replaces it during generation AND while
# any modal (/model, /context, /resume) is open. Both indicate "past the
# sign-in screen, harness is alive."
_IDLE_FOOTER_RE = re.compile(r"\?\s*for shortcuts", re.IGNORECASE)
_MODAL_FOOTER_RE = re.compile(r"esc to cancel", re.IGNORECASE)
_BUSY_RE = re.compile(r"Generating\.\.\.", re.IGNORECASE)

# First-launch trust dialog. The default selection is "Yes, I trust this
# folder", so a bare Enter accepts. Do NOT send "Up" first: at the top row
# the dialog wraps to "No, exit", which would terminate the harness.
_TRUST_PROMPT_RE = re.compile(
    r"Do you trust the contents|Yes, I trust this folder",
    re.IGNORECASE,
)


def _tail(pane_text: str) -> str:
    lines = pane_text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines[-_TAIL_LINES:])


class AntigravityAdapter(HarnessAdapter):
    kind: ClassVar[str] = "antigravity"
    crow_system_prompt: ClassVar[str] = "see prompts/crow_antigravity.md"
    # No usage slash command in 1.0.2 — `/context` shows a token meter for
    # the current conversation, not billing windows.
    # /model is a numbered picker of human labels with no id column, so
    # generic discovery yields noise; the curated list is empty until
    # the harness ships a stable model id.
    model_list_command: ClassVar[str | None] = None
    model_selection_command_template: ClassVar[str | None] = None
    available_startup_models: ClassVar[list[tuple[str, str]]] = []
    # Transcript parsing is best-effort; the prompt echo is "> …" and the
    # reply lives between marker lines. Header chrome (banner / email /
    # model / cwd) and the two footer variants are dropped.
    transcript_prompt_markers: ClassVar[tuple[str, ...]] = (">",)
    transcript_drop_substrings: ClassVar[tuple[str, ...]] = (
        "antigravity cli",
        "? for shortcuts",
        "esc to cancel",
        "↑/↓ navigate",
    )

    def startup_cmd(self, cwd: Path) -> list[str]:
        del cwd  # cwd is honoured by tmux.create_session
        # No --model flag in agy 1.0.2; startup_model is advisory only.
        return ["agy", "--dangerously-skip-permissions"]

    def is_ready(self, pane_text: str) -> bool:
        """True once the harness has booted past the sign-in spinner.

        Both REPL idle and modal/busy states qualify; the trust dialog
        also counts so :meth:`initialize_defaults` can dismiss it.
        """
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
        """True iff the REPL footer is visible and no generation is in flight."""
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _TRUST_PROMPT_RE.search(tail) or _BUSY_RE.search(tail):
            return False
        return bool(_IDLE_FOOTER_RE.search(tail))

    def is_busy(self, pane_text: str) -> bool:
        return bool(_BUSY_RE.search(_tail(strip_ansi(pane_text))))

    def extract_last_message(self, pane_text: str) -> str | None:
        return extract_last_message_heuristic(pane_text)

    async def initialize_defaults(self, session: str, spec: HarnessStartSpec) -> SimpleResult[None]:
        del spec
        # Dismiss the first-run "Do you trust the contents …" dialog if
        # present. Default selection is "Yes, I trust this folder"; "Up"
        # is a no-op when already on the first row but guards against
        # the cursor having drifted.
        for _ in range(15):  # ~6 s total
            try:
                pane = strip_ansi(await tmux.capture_pane(session, lines=40))
            except tmux.TmuxError:
                return ok_result()  # session gone; let wait_idle surface it
            if _TRUST_PROMPT_RE.search(pane):
                # Default selection is "Yes, I trust this folder" — bare
                # Enter accepts. Don't send "Up": at the top row it wraps
                # to "No, exit", which would terminate the harness on
                # first launch in an untrusted directory.
                await tmux.send_keys(session, "", literal=True, enter=True)
                await asyncio.sleep(0.6)
                return ok_result()
            if self.is_idle(pane):
                return ok_result()
            await asyncio.sleep(0.4)
        return ok_result()

    async def set_model(self, session: str, model: str, *, effort: str | None = None) -> bool:
        del session, effort
        # /model is a numbered picker of display labels; runtime selection
        # by id is unsupported. Accept only when the caller's desired
        # model already matches the harness default.
        return model == self.startup_model

    async def interrupt(self, session: str) -> None:
        # Busy state shows "esc to cancel" in the footer; Esc cancels.
        await self.interrupt_generation(session)
