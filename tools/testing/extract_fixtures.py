#!/usr/bin/env python3
"""Extract harness pane fixtures from tmux session recordings.

Reads ``tools/testing/recordings/<session>/frames.jsonl`` and writes redacted
plain-text panes under ``tests/fixtures/harness_panes/`` for harness unit tests.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RECORDINGS_DIR = REPO_ROOT / "tools" / "testing" / "recordings"
OUTPUT_DIR = REPO_ROOT / "tests" / "fixtures" / "harness_panes"

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_SHELL_USER_HOST_RE = re.compile(r"\b[A-Za-z0-9_-]+@[A-Za-z0-9_-]+\b")
_HOME_RE = re.compile(r"/home/[A-Za-z0-9_-]+")


@dataclass(frozen=True)
class Extraction:
    session: str
    output_name: str
    find_frame: Callable[[list[dict[str, object]]], int]


def _text(frames: list[dict[str, object]], index: int) -> str:
    raw = frames[index].get("text")
    return raw if isinstance(raw, str) else ""


def _find_first(
    frames: list[dict[str, object]],
    predicate: Callable[[str], bool],
    *,
    start: int = 0,
) -> int:
    for index in range(start, len(frames)):
        if predicate(_text(frames, index)):
            return index
    raise ValueError("no matching frame")


def _cc_idle(frames: list[dict[str, object]]) -> int:
    def ok(text: str) -> bool:
        lower = text.lower()
        return (
            "claude code" in lower
            and "bypass permissions" in lower
            and "esc to interrupt" not in lower
            and 'Try "create' in text
        )

    return _find_first(frames, ok, start=12)


def _cc_busy(frames: list[dict[str, object]]) -> int:
    def ok(text: str) -> bool:
        lower = text.lower()
        return "bypass permissions" in lower and "esc to interrupt" in lower

    return _find_first(frames, ok, start=20)


def _cc_startup(frames: list[dict[str, object]]) -> int:
    return _find_first(frames, lambda t: "Claude Code v2" in t)


def _codex_idle(frames: list[dict[str, object]]) -> int:
    def ok(text: str) -> bool:
        if "Press enter to confirm" in text or "/model  choose" in text:
            return False
        return (
            "OpenAI Codex" in text
            and "gpt-5.4 high" in text
            and re.search(r"^\s*›", text, re.MULTILINE) is not None
        )

    return _find_first(frames, ok, start=25)


def _codex_model_list(frames: list[dict[str, object]]) -> int:
    def ok(text: str) -> bool:
        return (
            "Press enter to confirm" in text
            and re.search(r"^\s*›\s*\d+\.\s+gpt-", text, re.MULTILINE) is not None
        )

    return _find_first(frames, ok, start=15)


def _codex_startup(frames: list[dict[str, object]]) -> int:
    return _find_first(
        frames,
        lambda t: "OpenAI Codex" in t and "model:" in t and "gpt-5" in t,
    )


def _cursor_idle(frames: list[dict[str, object]]) -> int:
    def ok(text: str) -> bool:
        return (
            "Plan, search" in text
            and "~/Documents/code/murder" in text
            and "Type to filter" not in text
            and "→ /m" not in text
        )

    return _find_first(frames, ok, start=12)


def _cursor_model_list(frames: list[dict[str, object]]) -> int:
    return _find_first(
        frames,
        lambda t: "Type to filter" in t and "Enter to select" in t,
    )


def _cursor_startup(frames: list[dict[str, object]]) -> int:
    return _find_first(
        frames,
        lambda t: "Cursor Agent" in t and "Plan, search" in t,
    )


def _pi_idle(frames: list[dict[str, object]]) -> int:
    gauge = re.compile(r"\d+(?:\.\d+)?%/\d+(?:\.\d+)?[kKmM]\s+\(auto\)")

    def ok(text: str) -> bool:
        if not gauge.search(text):
            return False
        lower = text.lower()
        if any(
            word in lower
            for word in ("thinking", "streaming", "running", "executing")
        ):
            return False
        if re.search(r"\(\d+/\d+\)", text):
            return False
        tail = "\n".join(text.splitlines()[-5:])
        return bool(gauge.search(tail))

    return _find_first(frames, ok, start=18)


def _pi_model_list(frames: list[dict[str, object]]) -> int:
    return _find_first(
        frames,
        lambda t: t.startswith("Model scope:") or "\nModel scope:" in t[:200],
    )


# ── New fixtures extracted from 2026-05-26 recordings ────────────────────────

def _codex_busy(frames: list[dict[str, object]]) -> int:
    """Frame where Codex shows its ``• Working`` busy indicator.

    Note: the current CodexAdapter._BUSY_RE does not match this ``•``-prefixed
    line (requires ``^\\s*working``), so ``is_idle=True`` on this fixture
    despite the agent actively generating. The test documents this known gap.
    """

    def ok(text: str) -> bool:
        lines = text.splitlines()
        tail = "\n".join(lines[-15:])
        return "• Working" in tail and "OpenAI Codex" in text

    return _find_first(frames, ok)


def _cursor_busy(frames: list[dict[str, object]]) -> int:
    """Frame where Cursor shows ``ctrl+c to stop`` + a spinner line."""

    def ok(text: str) -> bool:
        lines = text.splitlines()
        while lines and not lines[-1].strip():
            lines.pop()
        tail = "\n".join(lines[-20:])
        return "ctrl+c to stop" in tail.lower()

    return _find_first(frames, ok)


def _pi_busy(frames: list[dict[str, object]]) -> int:
    """Frame where Pi's tail contains a ``_BUSY_RE``-matching word (running/thinking/…).

    In the pi-busy recording the word ``running`` appears inside the agent's
    planning text ("I'll begin by *running* find …"), which is what currently
    makes PiAdapter.is_busy() return True — the status indicator itself
    (``⠸ Working…``) does not match the regex. Tests assert this real behavior.
    """

    def ok(text: str) -> bool:
        lines = text.splitlines()
        tail = "\n".join(lines[-30:])
        return bool(re.search(r"\b(thinking|streaming|running|executing)\b", tail, re.IGNORECASE))

    return _find_first(frames, ok)


def _cc_trust_dialog(frames: list[dict[str, object]]) -> int:
    """Frame showing CC's first-run ``trust this folder?`` dialog."""
    return _find_first(
        frames,
        lambda t: "Yes, I trust this folder" in t or "trust the files in this folder" in t.lower(),
    )


def _cc_multiturn_idle(frames: list[dict[str, object]]) -> int:
    """Frame with at least two completed CC turns and idle (no ``esc to interrupt``).

    Requires 3+ ``❯`` markers (2 completed prompt echoes + 1 live cursor)
    so we get a real multi-turn transcript, not just startup + empty cursor.
    """

    def ok(text: str) -> bool:
        lower = text.lower()
        return (
            "bypass permissions" in lower
            and "esc to interrupt" not in lower
            and text.count("❯") >= 3
        )

    return _find_first(frames, ok)


def _cc_interrupt(frames: list[dict[str, object]]) -> int:
    return _find_first(frames, lambda t: "interrupted" in t.lower() and "instead" in t.lower())


def _codex_interrupt(frames: list[dict[str, object]]) -> int:
    return _find_first(frames, lambda t: "conversation interrupted" in t.lower())


def _cursor_interrupt(frames: list[dict[str, object]]) -> int:
    def ok(text: str) -> bool:
        lower = text.lower()
        return "composing" not in lower and "cursor agent" in lower and len(text) > 400

    return _find_first(frames, ok, start=8)


def _pi_interrupt(frames: list[dict[str, object]]) -> int:
    return _find_first(frames, lambda t: "operation aborted" in t.lower())


def _pi_model_picker(frames: list[dict[str, object]]) -> int:
    """Frame showing Pi's interactive ``/model`` picker (``→ provider/model [tag]`` rows)."""

    def ok(text: str) -> bool:
        return bool(re.search(r"^→\s+\S+/\S+", text, re.MULTILINE)) and "Scope:" in text

    return _find_first(frames, ok)


EXTRACTIONS: tuple[Extraction, ...] = (
    Extraction("20260523-215258", "cc_idle.txt", _cc_idle),
    Extraction("20260523-215258", "cc_busy.txt", _cc_busy),
    Extraction("20260523-215258", "cc_startup.txt", _cc_startup),
    Extraction("20260523-215413", "codex_idle.txt", _codex_idle),
    Extraction("20260523-215413", "codex_model_list.txt", _codex_model_list),
    Extraction("20260523-215413", "codex_startup.txt", _codex_startup),
    Extraction("20260523-215643", "cursor_idle.txt", _cursor_idle),
    Extraction("20260523-215643", "cursor_model_list.txt", _cursor_model_list),
    Extraction("20260523-215643", "cursor_startup.txt", _cursor_startup),
    Extraction("20260523-215816", "pi_idle.txt", _pi_idle),
    Extraction("20260523-215816", "pi_model_list.txt", _pi_model_list),
    # 2026-05-26 additions: busy states, trust dialog, multiturn, Pi picker
    Extraction("20260526-122908-codex-busy-mini", "codex_busy.txt", _codex_busy),
    Extraction("20260526-102216-cursor-busy", "cursor_busy.txt", _cursor_busy),
    Extraction("20260526-103015-pi-busy-deepseek-v4-flash", "pi_busy.txt", _pi_busy),
    Extraction("20260526-111559-claude-trust-dialog-haiku", "cc_trust_dialog.txt", _cc_trust_dialog),
    Extraction("20260526-103649-claude-multiturn-haiku", "cc_multiturn_idle.txt", _cc_multiturn_idle),
    Extraction("20260526-080137-pi-model-deepseek-v4-flash", "pi_model_picker.txt", _pi_model_picker),
    Extraction("20260526-110300-claude-interrupt-haiku", "cc_interrupt.txt", _cc_interrupt),
    Extraction("20260526-123222-codex-interrupt-mini", "codex_interrupt.txt", _codex_interrupt),
    Extraction("20260526-105415-cursor-interrupt", "cursor_interrupt.txt", _cursor_interrupt),
    Extraction(
        "20260526-105818-pi-interrupt-deepseek-v4-flash",
        "pi_interrupt.txt",
        _pi_interrupt,
    ),
)


def redact(text: str) -> str:
    text = _EMAIL_RE.sub("example@website.com", text)
    text = _SHELL_USER_HOST_RE.sub("user@machine", text)
    return _HOME_RE.sub("/home/user", text)


def load_frames(session: str) -> list[dict[str, object]]:
    path = RECORDINGS_DIR / session / "frames.jsonl"
    frames: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if isinstance(row, dict):
            frames.append(row)
    if not frames:
        raise ValueError(f"no frames in {path}")
    return frames


def write_fixture(
    *,
    session: str,
    frame_index: int,
    output_name: str,
    text: str,
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / output_name
    source = f"tools/testing/recordings/{session}/frames.jsonl"
    header = f"# source: {source} frame {frame_index}\n"
    out_path.write_text(header + redact(text), encoding="utf-8")
    return out_path


def extract_session(session: str, items: list[Extraction]) -> bool:
    frames_path = RECORDINGS_DIR / session / "frames.jsonl"
    if not frames_path.is_file():
        print(
            f"error: missing recording {frames_path} "
            f"(skipping {len(items)} fixture(s) for session {session})",
            file=sys.stderr,
        )
        return False

    frames = load_frames(session)
    ok = True
    for item in items:
        try:
            frame_index = item.find_frame(frames)
        except ValueError as exc:
            print(
                f"error: {item.output_name}: {exc} in {frames_path}",
                file=sys.stderr,
            )
            ok = False
            continue
        out_path = write_fixture(
            session=session,
            frame_index=frame_index,
            output_name=item.output_name,
            text=_text(frames, frame_index),
        )
        print(f"{out_path} frame {frame_index}")
    return ok


def main() -> int:
    by_session: dict[str, list[Extraction]] = {}
    for item in EXTRACTIONS:
        by_session.setdefault(item.session, []).append(item)

    all_ok = True
    for session, items in by_session.items():
        if not extract_session(session, items):
            all_ok = False
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
