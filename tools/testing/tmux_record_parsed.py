#!/usr/bin/env python3
"""Live parsing recorder — drive a harness in tmux while the transcript parser
runs on every captured frame and writes the parsed doc to a JSON file.

This is ``tmux_record.py`` with the transcript parser wired in. You attach to a
real harness (claude_code / codex / cursor / pi / antigravity), drive it
normally, and a single capture loop:

    capture pane (production args) -> feed TranscriptAccumulator -> atomically
    rewrite ``--parsed-out`` (default tools/testing/live_parsed.json) -> append
    the raw frame to the recording's ``frames.jsonl``.

Watch the parsed output live in another terminal to catch parser bugs as they
happen, either with ``watch cat`` or — nicer — with ``liveparseview.py``:

    # terminal A
    python tools/testing/tmux_record_parsed.py --harness claude_code -- claude
    # terminal B
    python tools/testing/liveparseview.py

The capture command matches production exactly (``capture-pane -p [-e] -t
<session> -S -4000``) so divergences you see are real parser bugs, not capture
artifacts. The raw frames are saved to a standard recording dir, so a caught
bug can be turned into a fixture with ``extract_fixtures.py`` / annotated with
``annotate_frames.py`` using the captures the parser actually saw.

Caveat: production seeds the parser with ground-truth user turns from the DB
(``acc.user_texts``); this standalone tool has no DB, so user-turn parsing is
best-effort and may differ from production. That caveat is surfaced in the
parsed output so it is visible in the viewer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

# Reuse the PTY passthrough + tmux plumbing from the sibling recorder.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tmux_record as rec  # noqa: E402

from murder.llm.harnesses.transcripts import (  # noqa: E402
    TranscriptAccumulator,
    supports_harness,
    wants_ansi,
)
from murder.runtime.agents.base import TRANSCRIPT_SCROLLBACK_LINES  # noqa: E402

HARNESS_CHOICES = ("claude_code", "codex", "cursor", "pi", "antigravity")
USER_TEXTS_CAVEAT = (
    "(empty — standalone mode has no DB of ground-truth user turns; "
    "user-turn parsing is best-effort and may differ from production)"
)


def capture_pane_text(session: str, *, escapes: bool) -> str:
    """Capture the pane exactly as production does (tmux.py:capture_pane)."""
    extra = ["-e"] if escapes else []
    return rec.run_tmux(
        ["capture-pane", "-p", *extra, "-t", session, "-S", f"-{TRANSCRIPT_SCROLLBACK_LINES}"]
    ).stdout


def write_json_atomic(path: Path, payload: Any) -> None:
    """Rewrite ``path`` atomically so a watching reader never sees partial JSON."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class ParseSampler(threading.Thread):
    """One capture loop: feed the parser and persist the parsed doc + raw frames.

    A single loop guarantees the frame the parser saw is byte-identical to the
    frame appended to ``frames.jsonl``, so a caught bug replays faithfully.
    """

    def __init__(
        self,
        *,
        session: str,
        harness: str,
        started: Any,
        interval_s: float,
        parsed_path: Path,
        frames_path: Path,
        recording_dir: Path,
        stop_event: threading.Event,
        wake_event: threading.Event,
    ) -> None:
        super().__init__(daemon=True)
        self._session = session
        self._harness = harness
        self._escapes = wants_ansi(harness)
        self._recording_started = started
        self._interval_s = interval_s
        self._parsed_path = parsed_path
        self._frames_path = frames_path
        self._recording_dir = recording_dir
        self._stop_event = stop_event
        self._wake_event = wake_event
        self._acc = TranscriptAccumulator(harness)
        self._last_text: str | None = None
        self._frames_file = frames_path.open("w", encoding="utf-8")
        self.frame_count = 0
        self.errors: list[str] = []

    def run(self) -> None:
        try:
            self.capture_once(force=True)
            while not self._stop_event.is_set():
                woke = self._wake_event.wait(self._interval_s)
                self._wake_event.clear()
                self.capture_once(force=woke)
            self.capture_once(force=True)
        finally:
            self._frames_file.close()

    def capture_once(self, *, force: bool) -> None:
        try:
            text = capture_pane_text(self._session, escapes=self._escapes)
        except Exception as exc:  # pragma: no cover - manual tooling path
            self.errors.append(str(exc))
            return
        if not force and text == self._last_text:
            return
        self._last_text = text

        when = rec.utc_now()
        t_rel = round((when - self._recording_started).total_seconds(), 6)
        self._frames_file.write(
            json.dumps({"at": rec.iso_timestamp(when), "t_rel_s": t_rel, "text": text})
        )
        self._frames_file.write("\n")
        self._frames_file.flush()
        self.frame_count += 1

        self._acc.feed(text)
        doc = self._acc.to_dict()
        payload = {
            "_meta": {
                "harness": self._harness,
                "captured_at": rec.iso_timestamp(when),
                "t_rel_s": t_rel,
                "frame_index": self.frame_count,
                "escapes": self._escapes,
                "recording_dir": str(self._recording_dir),
                "user_texts": USER_TEXTS_CAVEAT,
            },
            **doc,
        }
        try:
            write_json_atomic(self._parsed_path, payload)
        except OSError as exc:  # pragma: no cover - manual tooling path
            self.errors.append(str(exc))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--harness",
        required=True,
        choices=HARNESS_CHOICES,
        help="Harness grammar to parse the pane with.",
    )
    parser.add_argument(
        "--parsed-out",
        type=Path,
        default=Path("tools/testing/live_parsed.json"),
        help="Stable path for the live parsed doc (watch this / point liveparseview at it).",
    )
    parser.add_argument("--session", help="Attach to this tmux session; create it if missing.")
    parser.add_argument(
        "--cwd",
        type=Path,
        default=Path.cwd(),
        help="Working directory for a new session (default: current directory).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("tools/testing/recordings"),
        help="Directory where the raw-frame recording is written.",
    )
    parser.add_argument(
        "--frame-interval",
        type=float,
        default=0.05,
        help="Seconds between pane captures while idle (default: 0.05, 20 Hz).",
    )
    parser.add_argument(
        "--keep-session",
        action="store_true",
        help="Do not kill a newly created tmux session after recording ends.",
    )
    parser.add_argument(
        "--label",
        help="Optional label used in the saved recording directory name.",
    )
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="Optional command for a new tmux session. Use `-- <cmd ...>`.",
    )
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    return args


def main() -> int:
    args = parse_args()
    if not supports_harness(args.harness):
        raise rec.TmuxRecordError(f"unsupported harness: {args.harness}")

    session = args.session or rec.make_session_name()
    started = rec.utc_now()
    cwd = args.cwd.resolve()
    output_dir = args.output_dir.resolve()
    parsed_path = args.parsed_out.resolve()
    parsed_path.parent.mkdir(parents=True, exist_ok=True)
    command = list(args.command)
    created_session = False

    exists = rec.session_exists(session)
    if exists and command:
        raise rec.TmuxRecordError(
            "cannot pass `-- <command>` when attaching to an existing tmux session"
        )
    if not exists:
        rec.create_session(session, cwd, command, rec.terminal_size())
        created_session = True

    slug_parts = [started.strftime("%Y%m%d-%H%M%S")]
    if args.label:
        slug_parts.append(rec.slugify(args.label))
    recording_dir = output_dir / "-".join(slug_parts)
    recording_dir.mkdir(parents=True, exist_ok=False)
    frames_path = recording_dir / "frames.jsonl"

    print(
        f"Recording+parsing tmux session `{session}` (harness={args.harness}).\n"
        f"  parsed doc  -> {parsed_path}\n"
        f"  raw frames  -> {frames_path}\n"
        f"Watch the parse live in another terminal:\n"
        f"  python tools/testing/liveparseview.py {parsed_path}\n"
        f"Detach with your usual tmux binding or exit the shell to stop.",
        file=sys.stderr,
    )

    stop_event = threading.Event()
    wake_event = threading.Event()
    sampler = ParseSampler(
        session=session,
        harness=args.harness,
        started=started,
        interval_s=max(0.05, float(args.frame_interval)),
        parsed_path=parsed_path,
        frames_path=frames_path,
        recording_dir=recording_dir,
        stop_event=stop_event,
        wake_event=wake_event,
    )
    sampler.start()

    status = 0
    passthrough = rec.PtyPassthrough(session, started)
    try:
        status = passthrough.run(wake_event)
    finally:
        stop_event.set()
        sampler.join(timeout=2.0)

    ended = rec.utc_now()
    metadata = {
        "session": session,
        "created_session": created_session,
        "cwd": str(cwd),
        "command": command,
        "command_shell": rec.shell_join(command) if command else None,
        "harness": args.harness,
        "started_at": rec.iso_timestamp(started),
        "ended_at": rec.iso_timestamp(ended),
        "duration_s": round((ended - started).total_seconds(), 3),
        "attach_exit_status": status,
        "attach_result": rec.decode_process_status(status),
        "frame_count": sampler.frame_count,
        "frame_errors": sampler.errors,
        "parsed_out": str(parsed_path),
    }
    rec.write_json(recording_dir / "metadata.json", metadata)

    if created_session and not args.keep_session:
        rec.kill_session(session)
    print(
        f"\nSaved {sampler.frame_count} frames to {recording_dir}\n"
        f"Final parsed doc at {parsed_path}",
        file=sys.stderr,
    )
    if sampler.errors:
        print(f"{len(sampler.errors)} capture/parse errors (see metadata.json)", file=sys.stderr)
    return status if status >= 0 else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except rec.TmuxRecordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
