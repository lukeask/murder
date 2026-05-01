"""Tmux subprocess helpers, idle detection, and output shaping."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time

SESSION_ENV = "TMUX_LLM_SESSION"

# Names matched against tmux pane_current_command basename (see command_base).
IDLE_SHELLS = frozenset(
    {
        "bash",
        "sh",
        "dash",
        "zsh",
        "fish",
        "ksh",
        "mksh",
        "tcsh",
        "csh",
        "nu",
    }
)

EXIT_LINE = re.compile(r"^__TMUX_LL_EXIT__ (\d+)\s*$")

# ~8 KiB truncation for Run()
RUN_MAX_BYTES = 8192
RUN_HEAD_LINES = 100
RUN_TAIL_LINES = 50

_ANSI_RE = re.compile(r"\x1B\[[\d;]*[A-Za-z]|\x1B\][^\x07]*\x07|\x1B[\[\]#][\d;?]*[^\x1B\x07]*")


def session_name() -> str | None:
    v = os.environ.get(SESSION_ENV, "").strip()
    return v or None


def run_tmux(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def window_target(session: str, window: int | str) -> str:
    if isinstance(window, int):
        return f"{session}:{window}"
    w = str(window).strip()
    if not w:
        return f"{session}:0"
    return f"{session}:{w}"


def pane_target(session: str, window: int | str) -> str:
    """Single-pane model: first (and only) pane in the window."""
    if isinstance(window, int):
        return f"{session}:{window}.0"
    w = str(window).strip() or "0"
    return f"{session}:{w}.0"


def tmux_err(cp: subprocess.CompletedProcess[str]) -> str:
    return (cp.stderr or cp.stdout or "tmux failed").strip()


def window_indices(session: str) -> set[int]:
    cp = run_tmux(["list-windows", "-t", session, "-F", "#{window_index}"])
    if cp.returncode != 0:
        return set()
    out: set[int] = set()
    for line in cp.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            out.add(int(line))
    return out


def window_exists(session: str, window: int | str) -> bool:
    if isinstance(window, int):
        return window in window_indices(session)
    w = str(window).strip()
    if w.isdigit():
        return int(w) in window_indices(session)
    cp = run_tmux(["list-windows", "-t", session, "-F", "#{window_name}"])
    if cp.returncode != 0:
        return False
    names = {line.strip() for line in cp.stdout.splitlines()}
    return w in names


def display_pane_command(session: str, window: int | str) -> tuple[str | None, str | None]:
    """Returns (command_string, error_line)."""
    t = pane_target(session, window)
    cp = run_tmux(["display-message", "-p", "-t", t, "-F", "#{pane_current_command}"])
    if cp.returncode != 0:
        return None, window_target_error(window, tmux_err(cp))
    return cp.stdout.strip(), None


def window_target_error(window: int | str, detail: str) -> str:
    """Map tmux stderr for a missing/invalid ``-t`` target to a one-line tool error."""
    if "can't find" in detail.lower() or "not found" in detail.lower():
        return f"error: window {window} does not exist"
    return f"error: window {window} does not exist ({detail})"


def command_base(cmd: str) -> str:
    c = cmd.strip()
    if not c:
        return "unknown"
    base = os.path.basename(c.split()[0])
    return base.lstrip("-") or "unknown"


def is_idle_shell(pane_cmd: str) -> bool:
    return command_base(pane_cmd).lower() in IDLE_SHELLS


def strip_exit_lines(text: str) -> tuple[str, int | None]:
    """Remove __TMUX_LL_EXIT__ lines; return (cleaned_text, last_exit_code)."""
    last_exit: int | None = None
    kept: list[str] = []
    for line in text.splitlines():
        m = EXIT_LINE.match(line)
        if m:
            last_exit = int(m.group(1))
            continue
        kept.append(line)
    return "\n".join(kept).rstrip("\n"), last_exit


def scrollback_has_exit_marker(text: str) -> bool:
    return any(EXIT_LINE.match(line) for line in text.splitlines())


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def trim_lines_ws(s: str) -> str:
    return "\n".join(line.rstrip() for line in s.splitlines())


def collapse_blank_runs(s: str) -> str:
    lines = s.splitlines()
    out: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 2:
                out.append(line)
        else:
            blank_run = 0
            out.append(line)
    return "\n".join(out)


def clean_run_output(raw: str) -> str:
    s = strip_ansi(raw)
    s = trim_lines_ws(s)
    s = collapse_blank_runs(s)
    return s.strip("\n")


def truncate_run_body(text: str) -> str:
    b = text.encode("utf-8")
    if len(b) <= RUN_MAX_BYTES:
        return text
    lines = text.splitlines()
    if len(lines) <= RUN_HEAD_LINES + RUN_TAIL_LINES:
        return text
    head = "\n".join(lines[:RUN_HEAD_LINES])
    tail = "\n".join(lines[-RUN_TAIL_LINES:])
    notice = (
        f"\n\n[... output truncated for Run (~{RUN_MAX_BYTES} bytes); "
        f"use Read with a larger lines value for full scrollback ...]\n\n"
    )
    return head + notice + tail


def capture_pane(session: str, window: int | str, lines: int) -> tuple[str, str | None]:
    """``lines`` is clamped to at least 1 (callers should validate for API-level errors)."""
    t = pane_target(session, window)
    start = f"-{max(1, lines)}"
    cp = run_tmux(["capture-pane", "-t", t, "-p", "-S", start])
    if cp.returncode != 0:
        return "", window_target_error(window, tmux_err(cp))
    return cp.stdout, None


def footer(session: str, window: int | str, body: str) -> str:
    """Append status line. Exit code in the idle case comes from the last ``__TMUX_LL_EXIT__`` line in *body* (visible scrollback only)."""
    cmd, err = display_pane_command(session, window)
    if err:
        return f"\n\n{err}"
    assert cmd is not None
    base = command_base(cmd)
    if is_idle_shell(cmd):
        _, last_exit = strip_exit_lines(body)
        if last_exit is not None:
            return f"\n\n[idle | {base} | exit {last_exit}]"
        return f"\n\n[idle | {base}]"
    return f"\n\n[busy | {base}]"


def wrap_shell_command(command: str) -> str:
    """
    Run via ``sh -c`` so POSIX ``{ …; }`` / ``$?`` work in fish, nu, tcsh, etc.

    Caveats: commands ending in ``&`` (background), unbalanced heredocs, or other
    shell metacharacter edge cases can still mis-record exit status; prefer simple
    one-liners or scripts in files. Very long one-liners can hit ``ARG_MAX``.
    """
    inner = "{ " + command + "; __tmux_ll_ec=$?; printf '\\n__TMUX_LL_EXIT__ %d\\n' $__tmux_ll_ec; }"
    return "sh -c " + shlex.quote(inner)


def send_keys_literal(session: str, window: int | str, text: str) -> str | None:
    t = pane_target(session, window)
    cp = run_tmux(["send-keys", "-t", t, "-l", text])
    if cp.returncode != 0:
        return window_target_error(window, tmux_err(cp))
    return None


def send_keys_named(session: str, window: int | str, *key_names: str) -> str | None:
    t = pane_target(session, window)
    cp = run_tmux(["send-keys", "-t", t, *key_names])
    if cp.returncode != 0:
        return window_target_error(window, tmux_err(cp))
    return None


def send_line_enter(session: str, window: int | str, line: str) -> str | None:
    """Submit one shell line: literal text plus a real newline in a single send-keys -l."""
    t = pane_target(session, window)
    cp = run_tmux(["send-keys", "-t", t, "-l", line + "\n"])
    if cp.returncode != 0:
        return window_target_error(window, tmux_err(cp))
    return None


def wait_idle(
    session: str,
    window: int | str,
    timeout: float,
    poll: float = 0.15,
    *,
    timeout_label: str = "Wait",
) -> tuple[bool, str | None]:
    """
    Returns (ok_idle, error_or_none).
    If timeout while busy, error describes busy state.
    """
    deadline = time.monotonic() + timeout
    last_cmd = ""
    while time.monotonic() < deadline:
        cmd, err = display_pane_command(session, window)
        if err:
            return False, err
        assert cmd is not None
        last_cmd = cmd
        if is_idle_shell(cmd):
            return True, None
        time.sleep(poll)
    proc = command_base(last_cmd)
    return False, (
        f"error: {timeout_label} timed out after {timeout:g}s; window still busy ({proc})"
    )
