#!/usr/bin/env python3
"""Pretty-print the live parsed transcript written by ``tmux_record_parsed.py``.

Reads the parsed JSON doc (default ``tools/testing/live_parsed.json``) and
renders its segments in a readable, colourised form. By default it polls the
file and re-renders whenever it changes — a built-in ``watch`` so you don't have
to read raw JSON. Use ``--once`` for a single render.

    python tools/testing/liveparseview.py
    python tools/testing/liveparseview.py path/to/parsed.json
    python tools/testing/liveparseview.py --once

Pair it with the recorder:

    # terminal A
    python tools/testing/tmux_record_parsed.py --harness claude_code -- claude
    # terminal B
    python tools/testing/liveparseview.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("tools/testing/live_parsed.json")
POLL_INTERVAL_S = 0.1

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
GREY = "\033[90m"

CLEAR = "\033[2J\033[H"


def _color(text: str, code: str, *, enable: bool) -> str:
    return f"{code}{text}{RESET}" if enable else text


def _indent(text: str, prefix: str = "    ") -> str:
    return "\n".join(prefix + line for line in text.splitlines()) or prefix.rstrip()


def _render_user(seg: dict[str, Any], *, color: bool) -> str:
    head = _color("USER", BOLD + CYAN, enable=color)
    return f"{head}\n{_indent(seg.get('text', ''))}"


def _render_assistant(seg: dict[str, Any], *, color: bool) -> str:
    tag = "ASSISTANT" + (" (final)" if seg.get("phase") == "final" else "")
    elapsed = seg.get("elapsed")
    suffix = f"  {_color(elapsed, GREY, enable=color)}" if elapsed else ""
    head = _color(tag, BOLD + GREEN, enable=color)
    return f"{head}{suffix}\n{_indent(seg.get('text', ''))}"


def _render_tool_call(seg: dict[str, Any], *, color: bool) -> str:
    flags = []
    if seg.get("running"):
        flags.append(_color("running", YELLOW, enable=color))
    if seg.get("elided"):
        flags.append(_color("elided", GREY, enable=color))
    flag_str = ("  " + " ".join(flags)) if flags else ""
    head = _color("TOOL", BOLD + MAGENTA, enable=color)
    lines = [f"{head} {seg.get('title', '')}{flag_str}"]
    if seg.get("input"):
        lines.append(_indent(seg["input"], "  in  | "))
    if seg.get("result"):
        lines.append(_indent(seg["result"], "  out | "))
    return "\n".join(lines)


def _render_plan_update(seg: dict[str, Any], *, color: bool) -> str:
    head = _color("PLAN", BOLD + BLUE, enable=color)
    lines = [f"{head} {seg.get('title', '')}"]
    for item in seg.get("items", []):
        box = "[x]" if item.get("done") else "[ ]"
        lines.append(f"    {box} {item.get('text', '')}")
    return "\n".join(lines)


def _render_agent_event(seg: dict[str, Any], *, color: bool) -> str:
    elapsed = seg.get("elapsed")
    suffix = f"  {_color(elapsed, GREY, enable=color)}" if elapsed else ""
    head = _color("AGENT", BOLD + YELLOW, enable=color)
    return f"{head} {seg.get('name', '')} — {seg.get('status', '')}{suffix}"


def _render_choice_prompt(seg: dict[str, Any], *, color: bool) -> str:
    head = _color("CHOICE", BOLD + RED, enable=color)
    lines = [f"{head} {seg.get('question', '')}"]
    selected, chosen = seg.get("selected"), seg.get("chosen")
    for opt in seg.get("options", []):
        num = opt.get("number")
        marker = ">" if num == selected else " "
        picked = _color(" <-- chosen", GREEN, enable=color) if num == chosen else ""
        lines.append(f"  {marker} {num}. {opt.get('label', '')}{picked}")
        if opt.get("description"):
            lines.append(_indent(opt["description"], "       "))
    if seg.get("footer"):
        lines.append(_indent(seg["footer"], "    "))
    if seg.get("answered"):
        lines.append(_color("    (answered)", GREY, enable=color))
    return "\n".join(lines)


_SEGMENT_RENDERERS = {
    "user": _render_user,
    "assistant": _render_assistant,
    "tool_call": _render_tool_call,
    "plan_update": _render_plan_update,
    "agent_event": _render_agent_event,
    "choice_prompt": _render_choice_prompt,
}


def render_segment(seg: dict[str, Any], *, color: bool) -> str:
    renderer = _SEGMENT_RENDERERS.get(seg.get("type", "?"))
    if renderer is None:
        return _color(f"{seg.get('type', '?')}: {json.dumps(seg)}", GREY, enable=color)
    return renderer(seg, color=color)


def render_doc(doc: dict[str, Any], *, color: bool) -> str:
    meta = doc.get("_meta", {})
    state = doc.get("state", "?")
    harness = doc.get("harness", meta.get("harness", "?"))
    segments = doc.get("segments", [])

    state_color = {
        "working": YELLOW,
        "awaiting_input": GREEN,
        "awaiting_approval": RED,
    }.get(state, GREY)

    header = (
        f"{_color(harness, BOLD, enable=color)}  "
        f"state={_color(state, state_color, enable=color)}  "
        f"segments={len(segments)}"
    )
    if meta:
        header += _color(
            f"  frame#{meta.get('frame_index', '?')} @ {meta.get('captured_at', '?')}",
            GREY,
            enable=color,
        )
    lines = [header]
    caveat = meta.get("user_texts")
    if caveat:
        lines.append(_color(f"user_texts {caveat}", DIM + YELLOW, enable=color))
    lines.append(_color("─" * 60, GREY, enable=color))

    if not segments:
        lines.append(_color("(no segments yet)", GREY, enable=color))
    for seg in segments:
        lines.append(render_segment(seg, color=color))
        lines.append("")
    return "\n".join(lines)


def load_doc(path: Path) -> dict[str, Any] | None:
    """Load the JSON doc, tolerating a mid-write read (atomic writer makes this
    rare, but a torn read just means we retry on the next poll)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=DEFAULT_PATH,
        help=f"Parsed JSON doc to view (default: {DEFAULT_PATH}).",
    )
    parser.add_argument("--once", action="store_true", help="Render once and exit.")
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colours.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    color = not args.no_color and sys.stdout.isatty()
    path = args.path

    if args.once:
        doc = load_doc(path)
        if doc is None:
            print(f"could not read {path}", file=sys.stderr)
            return 1
        print(render_doc(doc, color=color))
        return 0

    last_mtime = -1.0
    try:
        while True:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                sys.stdout.write(CLEAR + f"waiting for {path} ...\n")
                sys.stdout.flush()
                time.sleep(POLL_INTERVAL_S)
                continue
            if mtime != last_mtime:
                doc = load_doc(path)
                if doc is not None:
                    last_mtime = mtime
                    sys.stdout.write(CLEAR + render_doc(doc, color=color) + "\n")
                    sys.stdout.flush()
            time.sleep(POLL_INTERVAL_S)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
