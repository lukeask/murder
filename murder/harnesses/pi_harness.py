"""Pi coding-agent adapter (`pi`).

Pi's README and installed package docs were checked on 2026-05-02. The
adapter uses the CLI `--model` startup flag when a preferred startup model is
configured, because Pi's interactive `/model` command opens a selector UI.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar

from murder.harnesses.base import (
    HarnessAdapter,
)
from murder.harnesses.parsing import (
    extract_last_message_heuristic,
    is_rule_line,
    is_status_spinner_line,
    strip_ansi,
)

_TAIL_LINES = 30

# Pi's bottom status bar always shows a `0.0%/123k (auto)` context-budget gauge
# and the loaded model; either that or the input `>` / a `Ctrl+…` hint means
# the REPL is up. (Busy state is screened separately, before the idle check.)
_READY_RE = re.compile(
    r"(/hotkeys|Ctrl\+[A-Z]|Ctrl\+o|Slash commands|\d+%/\d+k|^\s*[>/]\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_IDLE_RE = re.compile(
    r"(/hotkeys|Ctrl\+[A-Z]|Ctrl\+o|\d+%/\d+k|^\s*[>/]\s*$)",
    re.IGNORECASE | re.MULTILINE,
)
_BUSY_RE = re.compile(
    r"\b(thinking|streaming|running|executing|tool calls?|retrying|compacting)\b",
    re.IGNORECASE,
)
_AUTH_RE = re.compile(
    r"\b(login|authenticate|api key|required|no provider|configure provider)\b",
    re.IGNORECASE,
)
_PI_STATUS_RE = re.compile(r"\b\d+(?:\.\d+)?%/\d+(?:\.\d+)?[kKmM]\s+\([^)]*\)")
_PI_CWD_RE = re.compile(r"^(?:~/|/|\./|\.\./).*(?:\s+\([^)]+\))?$")
_PI_CHROME_RE = re.compile(
    r"""
    ^\s*(?:
        pi\s+v\d+\b
        |escape\s+interrupt\b
        |press\s+ctrl\+o\b
        |pi\s+can\s+explain\b
        |extend\s+pi\.
        |warning:\s+tmux\s+extended-keys\b
        |scope:\s+all\s+\|\s+scoped\b
        |tab\s+scope\b
        |model\s+scope:
        |model\s+name:
        |use\s+/login\b
        |.*docs/(?:providers|models)\.md\s*$
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _is_pi_chrome(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if is_rule_line(line) or is_status_spinner_line(line):
        return True
    return bool(_PI_STATUS_RE.search(s) or _PI_CWD_RE.match(s) or _PI_CHROME_RE.match(s))


def _strip_pi_chrome(pane_text: str) -> str:
    return "\n".join(line for line in strip_ansi(pane_text).splitlines() if not _is_pi_chrome(line))


def _tail(pane_text: str) -> str:
    lines = pane_text.splitlines()
    return "\n".join(lines[-_TAIL_LINES:])


class PiAdapter(HarnessAdapter):
    kind: ClassVar[str] = "pi"
    crow_system_prompt: ClassVar[str] = "see prompts/crow_pi.md"
    # `/model` opens a picker listing `provider/model` ids (plus any local
    # weights) one per line; the generic parser handles it. The picker is a
    # modal — fine for the throwaway discovery session, which is killed after.
    model_list_command: ClassVar[str | None] = "/model"
    model_list_capture_delay_s: ClassVar[float] = 3.0
    # Pi echoes submitted prompts with `> ...`; the idle input prompt is a bare
    # `>` and is discarded by the shared parser. Pi-specific chrome is stripped
    # in parse_transcript before applying the generic prompt-marker heuristic.
    transcript_prompt_markers: ClassVar[tuple[str, ...]] = (">",)
    transcript_drop_substrings: ClassVar[tuple[str, ...]] = (
        "ctrl+c/ctrl+d",
        "slash commands",
        "startup help",
        "loaded resources",
        "tmux extended-keys",
        "0.0%/",
    )
    available_startup_models: ClassVar[list[tuple[str, str]]] = [
        ("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("anthropic/claude-opus-4-7", "Claude Opus 4.7"),
        ("openai/gpt-5.5", "GPT-5.5"),
        ("openai/gpt-5.4-mini", "GPT-5.4 Mini"),
    ]

    def startup_cmd(self, cwd: Path) -> list[str]:
        cmd = ["pi"]
        if self.startup_model:
            cmd.extend(["--model", self.startup_model])
        return cmd

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

    def format_nudge(self, msg: str) -> str:
        return f"[supervisor] {msg}"

    async def set_model(self, session: str, model: str) -> bool:
        del session
        return model == self.startup_model


from murder.harnesses.transcripts import PreprocessedPromptMarkerParser, register_parser

register_parser(
    "pi",
    PreprocessedPromptMarkerParser(
        prompt_markers=PiAdapter.transcript_prompt_markers,
        drop_substrings=PiAdapter.transcript_drop_substrings,
        normalize=_strip_pi_chrome,
    ),
)
