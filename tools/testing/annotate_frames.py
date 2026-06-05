#!/usr/bin/env python3
"""Step through tmux recording frames and annotate metadata.json per-frame comments.

Usage:
    python tools/testing/annotate_frames.py              # picker (repo root cwd)
    python tools/testing/annotate_frames.py RECORDING_DIR
    python tools/testing/annotate_frames.py path/to/frames.jsonl

Keys (scroll mode):
    h / l     previous / next frame (1-based frame numbers in saved comments)
    f         find — type digits, Enter to jump (e.g. f then 123 Enter)
    i         insert comment for current frame
    d         done — y/n, then s=save metadata / d=discard / n=keep editing

Keys (insert mode):
    Enter         finish — y/n to keep comment in memory
    Shift+Enter   newline (Ctrl+O fallback; Enter submits)
    Esc           cancel insert
"""

from __future__ import annotations

import argparse
import curses
import json
import re
from pathlib import Path

FRAME_SECTION = "\n\nFrame Comments:\n"
FRAME_LINE_RE = re.compile(r"^Frame (\d+): (.*)$")
RECORDINGS_DIR = Path("tools/testing/recordings")
PICKER_LIMIT = 10


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        default=None,
        help="Recording directory or frames.jsonl (omit for interactive picker)",
    )
    return parser.parse_args()


def _comment_one_line(comment: str, width: int) -> str:
    text = " ".join(comment.split())
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def list_recent_recordings(limit: int = PICKER_LIMIT) -> list[tuple[Path, str, str]]:
    if not RECORDINGS_DIR.is_dir():
        raise SystemExit(
            f"recordings not found at {RECORDINGS_DIR} — run from murder repo root"
        )
    candidates = [
        child
        for child in RECORDINGS_DIR.iterdir()
        if child.is_dir() and (child / "frames.jsonl").is_file()
    ]
    candidates.sort(key=lambda p: p.name, reverse=True)
    rows: list[tuple[Path, str, str]] = []
    for path in candidates[:limit]:
        comment = ""
        meta_path = path / "metadata.json"
        if meta_path.is_file():
            try:
                data = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            raw = data.get("comment", "")
            if isinstance(raw, str):
                comment = raw
        rows.append((path, path.name, comment))
    return rows


def pick_recording(stdscr: curses.window) -> Path | None:
    entries = list_recent_recordings()
    if not entries:
        raise SystemExit(f"no recordings under {RECORDINGS_DIR}")

    curses.curs_set(0)
    stdscr.keypad(True)
    selected = 0

    while True:
        _height, width = stdscr.getmaxyx()
        comment_width = max(10, width - 4)
        lines = ["Pick recording (↑/↓ or j/k, Enter, Esc=quit)", ""]
        for index, (_path, name, comment) in enumerate(entries):
            marker = ">" if index == selected else " "
            lines.append(f"{marker} {name}")
            preview = _comment_one_line(comment, comment_width) if comment else "(no comment)"
            lines.append(f"  {preview}")
            lines.append("")
        draw_pane(stdscr, lines)
        ch = stdscr.getch()
        if ch in (27, ord("q")):
            return None
        if ch in (curses.KEY_UP, ord("k")):
            selected = max(0, selected - 1)
        elif ch in (curses.KEY_DOWN, ord("j")):
            selected = min(len(entries) - 1, selected + 1)
        elif ch in (10, 13, curses.KEY_ENTER):
            return entries[selected][0]


def resolve_paths(path: Path) -> tuple[Path, Path]:
    path = path.resolve()
    if path.is_file() and path.name == "frames.jsonl":
        return path.parent, path
    if path.is_dir():
        frames = path / "frames.jsonl"
        if not frames.is_file():
            raise SystemExit(f"missing {frames}")
        return path, frames
    raise SystemExit(f"not a recording dir or frames.jsonl: {path}")


def load_frames(frames_path: Path) -> list[dict[str, object]]:
    frames: list[dict[str, object]] = []
    with frames_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                frames.append(json.loads(line))
    return frames


def load_metadata(recording_dir: Path) -> dict[str, object]:
    meta_path = recording_dir / "metadata.json"
    if not meta_path.is_file():
        raise SystemExit(f"missing {meta_path}")
    with meta_path.open(encoding="utf-8") as fh:
        return json.load(fh)


def write_metadata(recording_dir: Path, metadata: dict[str, object]) -> None:
    meta_path = recording_dir / "metadata.json"
    with meta_path.open("w", encoding="utf-8") as fh:
        json.dump(metadata, fh, indent=2)
        fh.write("\n")


def split_comment(comment: str) -> tuple[str, dict[int, str]]:
    if FRAME_SECTION not in comment:
        return comment.rstrip(), {}
    base, rest = comment.split(FRAME_SECTION, 1)
    base = base.rstrip()
    frame_comments: dict[int, str] = {}
    current_frame: int | None = None
    current_lines: list[str] = []
    for line in rest.splitlines():
        match = FRAME_LINE_RE.match(line)
        if match:
            if current_frame is not None:
                frame_comments[current_frame] = "\n".join(current_lines).strip()
            current_frame = int(match.group(1))
            current_lines = [match.group(2)]
        elif line.startswith("  ") and current_frame is not None:
            current_lines.append(line[2:])
        elif current_frame is not None and line.strip():
            current_lines.append(line)
    if current_frame is not None:
        frame_comments[current_frame] = "\n".join(current_lines).strip()
    return base, frame_comments


def build_comment(base: str, frame_comments: dict[int, str]) -> str:
    if not frame_comments:
        return base
    lines = [base, "", "Frame Comments:"]
    for number in sorted(frame_comments):
        text = frame_comments[number]
        if "\n" in text:
            parts = text.split("\n")
            lines.append(f"Frame {number}: {parts[0]}")
            for part in parts[1:]:
                lines.append(f"  {part}")
        else:
            lines.append(f"Frame {number}: {text}")
    return "\n".join(lines)


def frame_text(frame: dict[str, object]) -> str:
    raw = frame.get("text")
    if isinstance(raw, str):
        return raw.rstrip("\n")
    return ""


def init_curses_styles() -> None:
    if curses.has_colors():
        curses.start_color()
        curses.use_default_colors()


def draw_pane(stdscr: curses.window, lines: list[str]) -> None:
    height, width = stdscr.getmaxyx()
    stdscr.erase()
    row = 0
    for line in lines:
        if row >= height - 1:
            break
        try:
            stdscr.addnstr(row, 0, line, max(0, width - 1))
        except curses.error:
            pass
        row += 1
    stdscr.refresh()


def wrap_status(*parts: str) -> str:
    return "  |  ".join(parts)


def _truncate_lines(lines: list[str], budget: int) -> list[str]:
    if budget <= 0:
        return []
    if len(lines) <= budget:
        return lines
    if budget == 1:
        return [f"... ({len(lines)} lines)"]
    return lines[: budget - 1] + [f"... ({len(lines) - budget + 1} more lines)"]


def _put_line(
    stdscr: curses.window,
    row: int,
    text: str,
    *,
    width: int,
    dim: bool = False,
) -> None:
    if row < 0:
        return
    try:
        if dim:
            stdscr.attron(curses.A_DIM)
        stdscr.addnstr(row, 0, text, max(0, width - 1))
        if dim:
            stdscr.attroff(curses.A_DIM)
    except curses.error:
        pass


def build_header(
    frames: list[dict[str, object]],
    current_idx: int,
    *,
    frame_comments: dict[int, str],
    extra_status: str = "",
) -> str:
    total = len(frames)
    number = current_idx + 1
    header = f"Frame {number}/{total}"
    if number in frame_comments:
        header += "  [commented]"
    if extra_status:
        header += f"  |  {extra_status}"
    return header


def render_frame_view(
    stdscr: curses.window,
    frames: list[dict[str, object]],
    current_idx: int,
    *,
    frame_comments: dict[int, str],
    extra_status: str = "",
    edit: tuple[int, list[str], int] | None = None,
    edit_status: str = "",
) -> None:
    """Prev frame (dim) + current; edit block pinned to bottom when present."""
    height, width = stdscr.getmaxyx()
    stdscr.erase()

    header = build_header(
        frames, current_idx, frame_comments=frame_comments, extra_status=extra_status
    )
    _put_line(stdscr, 0, header, width=width)

    bottom = height - 1
    if edit is not None:
        frame_num, edit_lines, line_idx = edit
        visible = 8
        start = max(0, min(line_idx, len(edit_lines) - 1) - visible + 1)
        window = edit_lines[start : start + visible]
        edit_rows: list[str] = [
            f"--- comment frame {frame_num} (Enter=done, Ctrl+O=newline, Esc=cancel) ---"
        ]
        for offset, line in enumerate(window):
            abs_idx = start + offset
            prefix = "> " if abs_idx == line_idx else "  "
            edit_rows.append(prefix + line)
        if edit_status:
            edit_rows.append(edit_status)
        for line in reversed(edit_rows):
            _put_line(stdscr, bottom, line, width=width)
            bottom -= 1
        bottom -= 1

    current_num = current_idx + 1
    current_lines = frame_text(frames[current_idx]).splitlines()
    prev: tuple[int, list[str]] | None = None
    if current_idx > 0:
        prev_num = current_idx
        prev = (prev_num, frame_text(frames[current_idx - 1]).splitlines())

    frame_top = 2
    frame_bottom = bottom
    available = max(0, frame_bottom - frame_top)
    separator_rows = 2 if prev else 0
    min_current = min(len(current_lines), 6)
    prev_budget = 0
    if prev:
        prev_budget = min(
            len(prev[1]),
            max(3, (available - separator_rows - min_current) // 3),
        )
    current_budget = available - separator_rows - prev_budget
    if current_budget < min_current and prev:
        prev_budget = max(0, available - separator_rows - min_current)
        current_budget = available - separator_rows - prev_budget
    current_budget = max(0, current_budget)

    row = frame_top
    if prev and row <= frame_bottom:
        prev_num, prev_lines = prev
        shown_prev = _truncate_lines(prev_lines, prev_budget)
        _put_line(stdscr, row, f"--- frame {prev_num} ---", width=width, dim=True)
        row += 1
        for line in shown_prev:
            if row > frame_bottom:
                break
            _put_line(stdscr, row, line, width=width, dim=True)
            row += 1
        if row <= frame_bottom:
            row += 1

    if prev and row <= frame_bottom:
        _put_line(stdscr, row, "-----", width=width)
        row += 1
    shown_current = _truncate_lines(current_lines, current_budget)
    for line in shown_current:
        if row > frame_bottom:
            break
        _put_line(stdscr, row, line, width=width)
        row += 1

    stdscr.refresh()


def prompt_yn(stdscr: curses.window, message: str) -> bool:
    height, _width = stdscr.getmaxyx()
    row = max(0, height - 1)
    stdscr.move(row, 0)
    stdscr.clrtoeol()
    stdscr.addstr(row, 0, f"{message} [y/n] ")
    stdscr.refresh()
    while True:
        ch = stdscr.getch()
        if ch in (ord("y"), ord("Y")):
            return True
        if ch in (ord("n"), ord("N"), 27):
            return False


def prompt_save_discard(stdscr: curses.window) -> str | None:
    height, _width = stdscr.getmaxyx()
    row = max(0, height - 1)
    stdscr.move(row, 0)
    stdscr.clrtoeol()
    stdscr.addstr(
        row,
        0,
        "Save metadata comment? [s]ave / [d]iscard / [n]o (keep editing) ",
    )
    stdscr.refresh()
    while True:
        ch = stdscr.getch()
        if ch in (ord("s"), ord("S")):
            return "save"
        if ch in (ord("d"), ord("D")):
            return "discard"
        if ch in (ord("n"), ord("N"), 27):
            return None


def is_shift_enter(ch: int, prev: int | None) -> bool:
    if prev == 27 and ch in (10, 13):
        return True
    return ch in (337, 458)


def insert_comment_edit(
    stdscr: curses.window,
    frames: list[dict[str, object]],
    current_idx: int,
    frame_comments: dict[int, str],
    existing: str,
) -> str | None:
    number = current_idx + 1
    edit_lines = existing.split("\n") if existing else [""]
    line_idx = 0
    col = len(edit_lines[line_idx])

    def snapshot() -> str:
        return "\n".join(edit_lines).rstrip("\n")

    def render(status: str = "") -> None:
        render_frame_view(
            stdscr,
            frames,
            current_idx,
            frame_comments=frame_comments,
            edit=(number, edit_lines, line_idx),
            edit_status=status
            or "Shift+Enter/Ctrl+O=newline",
        )

    render()
    prev_ch: int | None = None

    while True:
        ch = stdscr.getch()
        if ch == 27:
            return None

        if is_shift_enter(ch, prev_ch) or ch == 15:  # Ctrl+O newline
            cur = edit_lines[line_idx]
            before, after = cur[:col], cur[col:]
            edit_lines[line_idx] = before
            edit_lines.insert(line_idx + 1, after)
            line_idx += 1
            col = 0
            render()
            prev_ch = ch
            continue

        if ch in (10, 13, curses.KEY_ENTER):
            text = snapshot()
            if prompt_yn(stdscr, "Save this comment to memory?"):
                return text
            render("not saved — keep editing")
            prev_ch = ch
            continue

        if ch in (curses.KEY_BACKSPACE, 127, 8):
            if col > 0:
                cur = edit_lines[line_idx]
                edit_lines[line_idx] = cur[: col - 1] + cur[col:]
                col -= 1
            elif line_idx > 0:
                col = len(edit_lines[line_idx - 1])
                edit_lines[line_idx - 1] += edit_lines[line_idx]
                del edit_lines[line_idx]
                line_idx -= 1
            render()
            prev_ch = ch
            continue

        if ch == curses.KEY_DC:
            cur = edit_lines[line_idx]
            if col < len(cur):
                edit_lines[line_idx] = cur[:col] + cur[col + 1 :]
            elif line_idx + 1 < len(edit_lines):
                edit_lines[line_idx] += edit_lines[line_idx + 1]
                del edit_lines[line_idx + 1]
            render()
            prev_ch = ch
            continue

        if ch == curses.KEY_LEFT:
            if col > 0:
                col -= 1
            elif line_idx > 0:
                line_idx -= 1
                col = len(edit_lines[line_idx])
            render()
            prev_ch = ch
            continue

        if ch == curses.KEY_RIGHT:
            if col < len(edit_lines[line_idx]):
                col += 1
            elif line_idx + 1 < len(edit_lines):
                line_idx += 1
                col = 0
            render()
            prev_ch = ch
            continue

        if ch == curses.KEY_UP and line_idx > 0:
            line_idx -= 1
            col = min(col, len(edit_lines[line_idx]))
            render()
            prev_ch = ch
            continue

        if ch == curses.KEY_DOWN and line_idx + 1 < len(edit_lines):
            line_idx += 1
            col = min(col, len(edit_lines[line_idx]))
            render()
            prev_ch = ch
            continue

        if 32 <= ch <= 126:
            cur = edit_lines[line_idx]
            edit_lines[line_idx] = cur[:col] + chr(ch) + cur[col:]
            col += 1
            render()
        prev_ch = ch


def run_ui(
    stdscr: curses.window,
    frames: list[dict[str, object]],
    base_comment: str,
    frame_comments: dict[int, str],
) -> tuple[str, dict[int, str], bool]:
    init_curses_styles()
    curses.curs_set(0)
    stdscr.keypad(True)

    current_idx = 0
    mode = "scroll"
    find_buffer = ""
    help_scroll = wrap_status("h/l=prev/next", "f=find", "i=comment", "d=done")

    while True:
        if mode == "find":
            status = f"find: {find_buffer}_  (Enter=go, Esc=cancel)"
        else:
            status = help_scroll
        render_frame_view(
            stdscr,
            frames,
            current_idx,
            frame_comments=frame_comments,
            extra_status=status,
        )
        ch = stdscr.getch()

        if mode == "find":
            if ch in (10, 13, curses.KEY_ENTER):
                if find_buffer.isdigit():
                    target = int(find_buffer)
                    if 1 <= target <= len(frames):
                        current_idx = target - 1
                find_buffer = ""
                mode = "scroll"
            elif ch == 27:
                find_buffer = ""
                mode = "scroll"
            elif ch in (curses.KEY_BACKSPACE, 127, 8):
                find_buffer = find_buffer[:-1]
            elif 48 <= ch <= 57:
                find_buffer += chr(ch)
            continue

        if ch == ord("f"):
            mode = "find"
            find_buffer = ""
            continue
        if ch == ord("h") or ch == curses.KEY_LEFT:
            current_idx = max(0, current_idx - 1)
            continue
        if ch == ord("l") or ch == curses.KEY_RIGHT:
            current_idx = min(len(frames) - 1, current_idx + 1)
            continue
        if ch == ord("i"):
            number = current_idx + 1
            existing = frame_comments.get(number, "")
            result = insert_comment_edit(
                stdscr,
                frames,
                current_idx,
                frame_comments,
                existing,
            )
            if result is not None:
                if result.strip():
                    frame_comments[number] = result
                elif number in frame_comments:
                    del frame_comments[number]
            continue
        if ch == ord("d"):
            if not prompt_yn(stdscr, "Done?"):
                continue
            action = prompt_save_discard(stdscr)
            if action == "save":
                return base_comment, frame_comments, True
            if action == "discard":
                return base_comment, frame_comments, False


def main() -> int:
    args = parse_args()
    path = args.path
    if path is None:

        def _pick(stdscr: curses.window) -> Path | None:
            return pick_recording(stdscr)

        try:
            path = curses.wrapper(_pick)
        except KeyboardInterrupt:
            return 130
        if path is None:
            return 0

    recording_dir, frames_path = resolve_paths(path)
    frames = load_frames(frames_path)
    if not frames:
        raise SystemExit("no frames in recording")

    metadata = load_metadata(recording_dir)
    raw_comment = metadata.get("comment", "")
    if not isinstance(raw_comment, str):
        raw_comment = ""
    base_comment, frame_comments = split_comment(raw_comment)

    saved = False

    def _run(stdscr: curses.window) -> None:
        nonlocal saved
        _base, _comments, should_save = run_ui(
            stdscr, frames, base_comment, frame_comments
        )
        if should_save:
            metadata["comment"] = build_comment(base_comment, frame_comments)
            write_metadata(recording_dir, metadata)
            saved = True

    try:
        curses.wrapper(_run)
    except KeyboardInterrupt:
        return 130

    if saved:
        print(f"Wrote {recording_dir / 'metadata.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
