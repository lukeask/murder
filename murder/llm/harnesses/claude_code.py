"""Claude Code CLI adapter (`claude --dangerously-skip-permissions`).

Caveats:
- `--dangerously-skip-permissions` refuses to run as root.
- CC's UI has tool-box rendering and spinners; pane parsing relies on
  empirically-tuned regexes (see the claude_code transcript grammar).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from murder.llm.harnesses.base import (
    HarnessAdapter,
    UsageCollectionMode,
)
from murder.llm.harnesses.models import HarnessModelState
from murder.llm.harnesses.parsing import (
    _claude_code_slash_id,
    extract_last_message_heuristic,
    normalize_effort,
    parse_claude_code_model_choices,
    strip_ansi,
)

_CC_MODEL_LINE_RE = re.compile(
    r"\b(?P<model>Opus|Sonnet|Haiku|Fable)\b(?:\s+\d+(?:\.\d+)*)?.*?"
    r"\bwith\s+(?P<effort>low|medium|high|x\s*high|xhigh|max)\s+effort\b",
    re.IGNORECASE,
)
# Fallback: banner line "Haiku 4.5 · Claude Pro" without effort text. The
# trailing " ·" anchors this to the banner/status line so a bare "Opus 4.x"
# mentioned in conversation prose can't be misread as the active model.
# Fable's banner version has no minor component ("Fable 5 · Claude Pro"),
# hence the optional decimal part.
_CC_BANNER_MODEL_RE = re.compile(
    r"\b(?P<model>Opus|Sonnet|Haiku|Fable)\s+\d+(?:\.\d+)*\s+·",
    re.IGNORECASE,
)
_CC_EFFORT_STATUS_RE = re.compile(
    r"[●•○◐◈]\s*(?P<effort>low|medium|high|x\s*high|xhigh|max)\s*(?:·|$)",
    re.IGNORECASE,
)
_CC_MENU_EFFORT_RE = re.compile(
    r"[●•○◐◈]\s*(?P<effort>low|medium|high|x\s*high|xhigh|max)\s+effort\b",
    re.IGNORECASE,
)
_TRUST_PROMPT_RE = re.compile(
    r"trust this folder|accessing workspace:|enter to confirm",
    re.IGNORECASE,
)


# Slash ids Claude Code's `/model <id>` accepts directly (confirmed against the
# live menu round-trip on v2.1.172): pass them through unchanged so a model id
# chosen from live discovery (e.g. `sonnet[1m]`, `opusplan`, `default`, `fable`)
# round-trips back to the exact slash arg.
_CC_DIRECT_SLASH_IDS = frozenset(
    {"default", "opus", "opusplan", "sonnet", "sonnet[1m]", "haiku", "fable"}
)


def _claude_model_id(model: str | None) -> str | None:
    """Map a chosen/stored model back to the `/model <id>` slash arg.

    Accepts both a slash id already produced by live discovery (passed through
    unchanged, so `sonnet[1m]`/`opusplan`/`default`/`fable` round-trip) and a
    human label/banner string, which is run through the same label→id derivation
    the menu parser uses.
    """
    if model is None:
        return None
    lowered = model.strip().lower()
    if not lowered:
        return None
    if lowered in _CC_DIRECT_SLASH_IDS:
        return lowered
    return _claude_code_slash_id(lowered)


class ClaudeCodeAdapter(HarnessAdapter):
    kind: ClassVar[str] = "claude_code"
    usage_collection_mode: ClassVar[UsageCollectionMode] = "tmux_slash"
    supported_efforts: ClassVar[tuple[str, ...]] = ("low", "medium", "high", "xhigh", "max")

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
    crow_system_prompt: ClassVar[str] = "see prompts/crow_claude_code.md"
    # Last-good fallback shown before live `/model` discovery resolves. Mirrors
    # the full set Claude Code's menu presents (v2.1.172 capture, 2026-06-10):
    # id is the `/model <id>` slash arg, label is the menu row label.
    available_startup_models: ClassVar[list[tuple[str, str]]] = [
        ("default", "Default (recommended)"),
        ("sonnet[1m]", "Sonnet (1M context)"),
        ("fable", "Fable"),
        ("opus", "Opus"),
        ("haiku", "Haiku"),
    ]

    def startup_cmd(self, cwd: Path) -> list[str]:
        del cwd
        cmd = ["claude", "--dangerously-skip-permissions"]
        # Resume a prior CC session in place when the orchestrator launched this
        # adapter from the history /resume path (set via HarnessStartSpec).
        if self.resume_session_id:
            cmd += ["--resume", self.resume_session_id]
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

    def extract_last_message(self, pane_text: str) -> str | None:
        return extract_last_message_heuristic(pane_text)

    _RESUME_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"claude\s+(?:--resume|-r)\s+(\S+)", re.IGNORECASE
    )
    _INVALID_RESUME_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"No conversation found with session ID|No sessions match",
        re.IGNORECASE,
    )

    def graceful_exit_command(self) -> str | None:
        return "/exit"

    def detects_invalid_resume(self, pane_text: str) -> bool:
        return bool(self._INVALID_RESUME_RE.search(strip_ansi(pane_text)))

    def extract_resume_session_id(self, pane_text: str) -> str | None:
        clean = strip_ansi(pane_text)
        m = self._RESUME_RE.search(clean)
        return m.group(1) if m else None

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
