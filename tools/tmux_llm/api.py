"""OpenAI-style tool definitions and dispatch for the tmux tool contract."""

from __future__ import annotations

import json
import time
from typing import Any

from . import backend as B
from .keys import parse_send_keys

# OpenAI Chat Completions `tools=[...]` shape (standard function-calling wire format).
ToolDefinition = dict[str, Any]

_ALLOWED_KEYS: dict[str, frozenset[str]] = {
    "WindowNew": frozenset({"name"}),
    "WindowRename": frozenset({"name", "window"}),
    "WindowList": frozenset(),
    "Run": frozenset({"command", "window", "wait"}),
    "Wait": frozenset({"window", "timeout"}),
    "Send": frozenset({"keys", "window"}),
    "Read": frozenset({"lines", "window"}),
}


def _unknown_arg_error(tool: str, raw: dict[str, Any]) -> str | None:
    allowed = _ALLOWED_KEYS.get(tool)
    if allowed is None:
        return None
    for k in raw:
        if k not in allowed:
            return f"error: unknown argument {k!r} for {tool}"
    return None


def normalize_window(window: int | str) -> int | str:
    if isinstance(window, int):
        return window
    w = str(window).strip()
    if w.isdigit():
        return int(w)
    return w


def _window_label(window: int | str) -> str:
    w = normalize_window(window)
    return str(w)


def _visible_from_raw(raw: str) -> str:
    body, _ = B.strip_exit_lines(raw)
    return B.clean_run_output(body)


def _require_session() -> str | None:
    return B.session_name()


def tool_window_new(name: str | None = None) -> str:
    s = _require_session()
    if not s:
        return "error: TMUX_LLM_SESSION is not set"
    args = ["new-window", "-t", s, "-P", "-F", "#{window_index}"]
    if name:
        args.extend(["-n", name])
    cp = B.run_tmux(args)
    if cp.returncode != 0:
        return f"error: {B.tmux_err(cp)}"
    idx = cp.stdout.strip()
    if not idx.isdigit():
        return f"error: unexpected tmux output: {cp.stdout!r}"
    return f"window {idx} created"


def tool_window_rename(name: str, window: int | str = 0) -> str:
    s = _require_session()
    if not s:
        return "error: TMUX_LLM_SESSION is not set"
    w = normalize_window(window)
    t = B.window_target(s, w)
    cp = B.run_tmux(["rename-window", "-t", t, name])
    if cp.returncode != 0:
        return B.window_target_error(w, B.tmux_err(cp))
    return f"window {_window_label(w)} renamed to {name!r}"


def tool_window_list() -> str:
    s = _require_session()
    if not s:
        return "error: TMUX_LLM_SESSION is not set"
    cp = B.run_tmux(
        ["list-windows", "-t", s, "-F", "#{window_index}\t#{window_name}\t#{pane_current_command}"]
    )
    if cp.returncode != 0:
        return f"error: {B.tmux_err(cp)}"
    lines_out: list[str] = []
    for line in cp.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        idx, wname, pcmd = parts[0], parts[1], parts[2]
        state = "idle" if B.is_idle_shell(pcmd) else "busy"
        proc = B.command_base(pcmd)
        lines_out.append(f"  {idx}  {wname:8}  {state:4}  {proc}")
    if not lines_out:
        return "(no windows)"
    return "\n".join(lines_out)


def _busy_guard(s: str, window: int | str) -> str | None:
    cmd, err = B.display_pane_command(s, window)
    if err:
        return err
    assert cmd is not None
    if not B.is_idle_shell(cmd):
        proc = B.command_base(cmd)
        return f"error: window {_window_label(window)} is busy ({proc}) — interrupt or use another window"
    return None


def _recapture_if_idle_missing_exit(
    session: str, window: int | str, raw: str, lines: int
) -> str:
    """One short retry when the shell is idle but the exit marker has not appeared yet."""
    body, ex = B.strip_exit_lines(raw)
    if ex is not None:
        return raw
    cmd, err = B.display_pane_command(session, window)
    if err or cmd is None or not B.is_idle_shell(cmd):
        return raw
    if B.scrollback_has_exit_marker(raw):
        return raw
    time.sleep(0.08)
    raw2, err2 = B.capture_pane(session, window, lines)
    if err2:
        return raw
    return raw2


def tool_run(command: str, window: int | str = 0, wait: float | None = 120) -> str:
    s = _require_session()
    if not s:
        return "error: TMUX_LLM_SESSION is not set"
    w = normalize_window(window)
    if not B.window_exists(s, w):
        return f"error: window {_window_label(w)} does not exist"
    if err := _busy_guard(s, w):
        return err

    wrapped = B.wrap_shell_command(command)
    if err := B.send_line_enter(s, w, wrapped):
        return err

    # Let the pty apply the line before polling pane_current_command; otherwise
    # wait_idle can observe the pre-command idle shell and return immediately.
    if wait is not None:
        time.sleep(0.15)

    if wait is None:
        raw, cap_err = B.capture_pane(s, w, 400)
        if cap_err:
            return cap_err
        clean = _visible_from_raw(raw)
        foot = B.footer(s, w, raw)
        return clean + foot

    ok, werr = B.wait_idle(s, w, float(wait), timeout_label="Run")
    if not ok:
        return werr or "error: wait failed"

    raw, cap_err = B.capture_pane(s, w, 400)
    if cap_err:
        return cap_err
    raw = _recapture_if_idle_missing_exit(s, w, raw, 400)
    body, _exit = B.strip_exit_lines(raw)
    clean = B.clean_run_output(body)
    clean = B.truncate_run_body(clean)
    foot = B.footer(s, w, raw)
    return clean + foot


def tool_wait(window: int | str = 0, timeout: float = 120) -> str:
    s = _require_session()
    if not s:
        return "error: TMUX_LLM_SESSION is not set"
    w = normalize_window(window)
    if not B.window_exists(s, w):
        return f"error: window {_window_label(w)} does not exist"

    cmd0, err0 = B.display_pane_command(s, w)
    if err0:
        return err0
    assert cmd0 is not None
    if B.is_idle_shell(cmd0):
        raw, cap_err = B.capture_pane(s, w, 400)
        if cap_err:
            return cap_err
        clean = _visible_from_raw(raw)
        foot = B.footer(s, w, raw)
        return clean + foot

    ok, werr = B.wait_idle(s, w, float(timeout))
    if not ok:
        return werr or "error: wait failed"

    raw, cap_err = B.capture_pane(s, w, 400)
    if cap_err:
        return cap_err
    clean = _visible_from_raw(raw)
    foot = B.footer(s, w, raw)
    return clean + foot


def tool_send(keys: str, window: int | str = 0) -> str:
    s = _require_session()
    if not s:
        return "error: TMUX_LLM_SESSION is not set"
    w = normalize_window(window)
    if not B.window_exists(s, w):
        return f"error: window {_window_label(w)} does not exist"

    segments, perr = parse_send_keys(keys)
    if perr:
        return perr

    total = len(segments)
    for i, (kind, payload) in enumerate(segments, start=1):
        if kind == "literal":
            if payload and (e := B.send_keys_literal(s, w, payload)):
                return f"{e} (Send segment {i}/{total}, literal)"
        else:
            if e := B.send_keys_named(s, w, payload):
                return f"{e} (Send segment {i}/{total}, key {payload!r})"

    return f"sent to window {_window_label(w)}"


def tool_read(lines: int = 200, window: int | str = 0) -> str:
    s = _require_session()
    if not s:
        return "error: TMUX_LLM_SESSION is not set"
    if isinstance(lines, bool):
        return "error: lines must be an integer >= 1"
    try:
        nlines = int(lines)
    except (TypeError, ValueError):
        return "error: lines must be an integer >= 1"
    if nlines < 1:
        return "error: lines must be >= 1"
    w = normalize_window(window)
    if not B.window_exists(s, w):
        return f"error: window {_window_label(w)} does not exist"

    raw, cap_err = B.capture_pane(s, w, nlines)
    if cap_err:
        return cap_err
    clean = _visible_from_raw(raw)
    foot = B.footer(s, w, raw)
    return clean + foot


def _schema_window() -> dict[str, Any]:
    return {"oneOf": [{"type": "integer"}, {"type": "string"}], "description": "0-based index or window name"}


OPENAI_TOOL_DEFINITIONS: list[ToolDefinition] = [
    {
        "type": "function",
        "function": {
            "name": "WindowNew",
            "description": "Open a new tmux window in the assigned session. Returns the new window index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": ["string", "null"], "description": "Optional window label."},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "WindowRename",
            "description": "Set a window's name for legible WindowList output.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "window": {**_schema_window(), "default": 0},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "WindowList",
            "description": "List windows with index, name, idle/busy, and current process.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Run",
            "description": (
                "Send a shell command plus Enter. Blocking wait (default 120s) until idle; "
                "wait=null fires and returns immediately. Session from TMUX_LLM_SESSION."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "window": {**_schema_window(), "default": 0},
                    "wait": {
                        "oneOf": [{"type": "number", "minimum": 0}, {"type": "null"}],
                        "default": 120,
                        "description": "Seconds to block until idle, or null for background.",
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Wait",
            "description": "Block until the window is idle, then return tail output and exit status footer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "window": {**_schema_window(), "default": 0},
                    "timeout": {"type": "number", "minimum": 0, "default": 120},
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Send",
            "description": (
                "Send keystrokes without Enter. Use <Enter>, <Esc>, <C-c>, etc. "
                "See tools/tmux.md for full token list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keys": {"type": "string"},
                    "window": {**_schema_window(), "default": 0},
                },
                "required": ["keys"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "Read",
            "description": "Capture terminal scrollback; no Run-style truncation. Ends with status footer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lines": {"type": "integer", "minimum": 1, "default": 200},
                    "window": {**_schema_window(), "default": 0},
                },
                "additionalProperties": False,
            },
        },
    },
]


def dispatch_tool(name: str, arguments: dict[str, Any] | None) -> str:
    """
    Execute one tool by name. Always returns a string (including `error: ...` lines).
    `arguments` is the JSON object from the model (may be None or empty).
    """
    args = arguments or {}
    if err := _unknown_arg_error(name, args):
        return err
    try:
        if name == "WindowNew":
            return tool_window_new(**_pick(args, ("name",), defaults={"name": None}))
        if name == "WindowRename":
            return tool_window_rename(**_pick(args, ("name", "window"), defaults={"window": 0}))
        if name == "WindowList":
            return tool_window_list()
        if name == "Run":
            return tool_run(
                **_pick(args, ("command", "window", "wait"), defaults={"window": 0, "wait": 120})
            )
        if name == "Wait":
            return tool_wait(**_pick(args, ("window", "timeout"), defaults={"window": 0, "timeout": 120}))
        if name == "Send":
            return tool_send(**_pick(args, ("keys", "window"), defaults={"window": 0}))
        if name == "Read":
            return tool_read(**_pick(args, ("lines", "window"), defaults={"lines": 200, "window": 0}))
    except TypeError as e:
        return f"error: bad arguments for {name}: {e}"

    return f"error: unknown tool {name!r}"


def _pick(
    raw: dict[str, Any],
    keys: tuple[str, ...],
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    defaults = defaults or {}
    out: dict[str, Any] = {}
    for k in keys:
        if k in raw:
            out[k] = raw[k]
        elif k in defaults:
            out[k] = defaults[k]
    return out


def dispatch_tool_call_json(payload: str) -> str:
    """Parse a JSON object {\"name\": str, \"arguments\": object|string} and dispatch."""
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError as e:
        return f"error: invalid JSON: {e}"
    name = obj.get("name")
    if not isinstance(name, str):
        return "error: missing string field 'name'"
    raw_args = obj.get("arguments", {})
    if isinstance(raw_args, str):
        try:
            raw_args = json.loads(raw_args) if raw_args.strip() else {}
        except json.JSONDecodeError as e:
            return f"error: arguments is not valid JSON: {e}"
    if raw_args is None:
        raw_args = {}
    if not isinstance(raw_args, dict):
        return "error: 'arguments' must be a JSON object"
    return dispatch_tool(name, raw_args)


def anthropic_input_schemas() -> list[dict[str, Any]]:
    """Same tools in Anthropic Messages `tools[].input_schema` shape."""
    out: list[dict[str, Any]] = []
    for t in OPENAI_TOOL_DEFINITIONS:
        fn = t["function"]
        out.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "input_schema": fn["parameters"],
            }
        )
    return out
