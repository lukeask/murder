"""Codex CLI adapter (`codex --no-alt-screen ...`).

Pane regexes were checked against `codex v0.128.0` on 2026-05-02. The
adapter runs Codex in inline mode so tmux capture-pane can see the live UI
instead of an alternate-screen buffer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import ClassVar

from murder.llm.harnesses.base import (
    HarnessAdapter,
    UsageCollectionMode,
)
from murder.llm.harnesses.models import HarnessModelState
from murder.llm.harnesses.parsing import (
    extract_last_message_heuristic,
    normalize_effort,
    strip_ansi,
)

_TAIL_LINES = 30

_BANNER_RE = re.compile(r"OpenAI Codex", re.IGNORECASE)
_MODEL_LOADING_RE = re.compile(r"\bmodel:\s*loading\b", re.IGNORECASE)
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
# Codex's live working spinner always renders as a status line carrying the
# "esc to interrupt" hint, e.g. `• Working (3s • esc to interrupt)` or
# `• Starting MCP servers (0/2): … (0s • esc to interrupt)`. The bare verb list
# the old regex used (`working|running|thinking|…` at the start of a `•` line)
# ALSO matched ordinary assistant prose narration — codex routinely opens a turn
# with `• Running the requested shell command…` / `• Processing the results…`.
# That false positive kept `is_busy` True (and `is_idle` False) on the COMPLETED
# idle frame, so the final reply never sealed and never delivered until the next
# turn pushed the prose out of the tail window (BUG-11). "esc to interrupt" is
# the unambiguous, version-stable live-spinner marker — verified live against
# codex 0.142.0 (2026-06-23) and across the recorded busy fixtures — so require
# it. (A genuine spinner line always carries it; assistant prose never does.)
_BUSY_RE = re.compile(r"esc to interrupt", re.IGNORECASE)
_LOGIN_RE = re.compile(r"\b(login required|not logged in|codex login)\b", re.IGNORECASE)
_TRUST_PROMPT_RE = re.compile(
    r"Do you trust the contents of this directory\?", re.IGNORECASE
)
_INVALID_RESUME_RE = re.compile(r"No saved session found with ID", re.IGNORECASE)
# codex's blocking "update available" menu (full-screen on launch). Its default
# option renders as "› 1. Update now (runs `npm install -g @openai/codex`)",
# whose leading "›" otherwise collides with the idle-prompt glyph and whose
# default selection, if Enter is pressed, upgrades the user's global codex.
# Recognize it explicitly: the dangerous "Update now" option that runs npm.
_UPDATE_MENU_RE = re.compile(
    r"^\s*[›>]?\s*\d+\.\s+Update now\b.*npm install",
    re.IGNORECASE | re.MULTILINE,
)
# A codex menu OPTION line ("› 1. Update now", "  2. Skip"). Its leading pointer
# glyph collides with the idle-prompt `›`, so when checking whether a live
# composer prompt sits below the update menu we must not count the menu's own
# option lines as that prompt.
_MENU_OPTION_RE = re.compile(r"^\s*[›>]?\s*\d+\.\s")
# The codex update menu's blocking action line. A genuine live/dismissed-in-
# scrollback menu always renders this; a prompt or transcript line that merely
# QUOTES "N. Update now (runs npm install ...)" does not. Requiring it stops
# arbitrary composer/transcript content from being misread as a live menu.
_MENU_SENTINEL_RE = re.compile(r"Press enter to continue", re.IGNORECASE)


def _tail(pane_text: str) -> str:
    lines = pane_text.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines[-_TAIL_LINES:])


def _model_is_loading(pane_text: str) -> bool:
    """Check the newest banner readback, ignoring older inline scrollback."""

    start = pane_text.casefold().rfind("model:")
    return start >= 0 and _MODEL_LOADING_RE.search(pane_text, start) is not None


def _update_menu_active(clean: str) -> bool:
    """True when codex's blocking 'update available' menu is the live surface.

    A dismissed menu lingers in tmux scrollback; the live `›` composer prompt
    then sits BELOW it, so the menu is normally 'active' only when no idle
    prompt follows the last menu match. Two glyph/content hazards are guarded:
    (1) the menu's own pointer glyph is also `›` (codex renders it on the
    selected option, e.g. "› 2. Skip"), so only a `›` line that is NOT a
    numbered menu option counts as the live composer prompt; (2) `_UPDATE_MENU_RE`
    matches any "N. Update now ... npm install" line, including a prompt or
    transcript line that merely QUOTES the menu — so we additionally require the
    menu's blocking sentinel ("Press enter to continue") at/after the match.
    """
    menu = list(_UPDATE_MENU_RE.finditer(clean))
    if not menu:
        return False
    last_menu = menu[-1].start()
    sentinel = _MENU_SENTINEL_RE.search(clean, last_menu)
    if sentinel is None:
        return False  # no blocking sentinel → quoted text, not a live menu
    for m in _IDLE_PROMPT_RE.finditer(clean):
        if m.start() <= last_menu:
            continue
        if _MENU_OPTION_RE.match(clean[m.start() : m.end()]):
            continue
        return False  # a real live composer prompt below the menu → it's gone
    return True


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


class CodexAdapter(HarnessAdapter):
    kind: ClassVar[str] = "codex"
    usage_collection_mode: ClassVar[UsageCollectionMode] = "tmux_slash"
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
        base_flags = [
            "--no-alt-screen",
            "--config",
            f"projects.{json.dumps(str(cwd))}.trust_level=\"untrusted\"",
            "--sandbox",
            "workspace-write",
            "--ask-for-approval",
            "never",
        ]
        cmd = ["codex", "resume", *base_flags] if self.resume_session_id else ["codex", *base_flags]
        for path in self.additional_workspace_dirs:
            cmd.extend(["--add-dir", str(path)])
        if self.resume_session_id:
            cmd.append(self.resume_session_id)
        return cmd

    def is_ready(self, pane_text: str) -> bool:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if _LOGIN_RE.search(tail) or _TRUST_PROMPT_RE.search(tail):
            return False
        # Codex paints the banner and composer before its model is bound.
        # Input sent during this short window is silently discarded.
        if _model_is_loading(clean):
            return False
        # A startup update menu is a blocking surface.  Verified restoration
        # owns any dismissal; this passive adapter only recognizes it.
        return bool(
            _update_menu_active(clean) or _BANNER_RE.search(clean) or _IDLE_PROMPT_RE.search(tail)
        )

    def is_idle(self, pane_text: str) -> bool:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if (
            _LOGIN_RE.search(tail)
            or _TRUST_PROMPT_RE.search(tail)
            or _update_menu_active(clean)
            or self.is_busy(tail)
        ):
            return False
        return bool(_IDLE_PROMPT_RE.search(tail))

    def is_input_ready(self, pane_text: str) -> bool | None:
        clean = strip_ansi(pane_text)
        tail = _tail(clean)
        if (
            _LOGIN_RE.search(tail)
            or _TRUST_PROMPT_RE.search(tail)
            or _update_menu_active(clean)
            or self.is_busy(tail)
        ):
            return False
        return _live_prompt_text(clean) is not None

    def is_busy(self, pane_text: str) -> bool:
        return bool(_BUSY_RE.search(_tail(strip_ansi(pane_text))))

    def detects_invalid_resume(self, pane_text: str) -> bool:
        return bool(_INVALID_RESUME_RE.search(strip_ansi(pane_text)))

    def extract_last_message(self, pane_text: str) -> str | None:
        return extract_last_message_heuristic(pane_text)

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
