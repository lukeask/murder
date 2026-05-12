from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_MODEL_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:+-]*(?:/[A-Za-z0-9][A-Za-z0-9._:+-]*)?")
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
_PAREN_ID_RE = re.compile(r"\(([A-Za-z0-9][A-Za-z0-9._:+/-]+)\)")
# Leading list/selection ornaments seen across harness `/model` UIs:
# Codex `› 1. `, Claude `❯ 2. ` / `  1. `, Cursor / pi `→ `, bullets, checkmarks.
_LIST_LEADER_RE = re.compile(r"^[\s>›→▸▶❯»\*•·●○◉◆◇■□☑☒✓✔✗✘\-]+")
_LIST_NUMBER_RE = re.compile(r"^\(?\d{1,3}[.)\]]\s+")
# Trailing/embedded selection markers and source/metadata tags:
#   `(current)` `✔` `✓` `[or]` `[inferno]` `(Thinking) 200K Medium` `· ~2× usage`
_MODEL_TAG_RE = re.compile(
    r"\s*(?:"
    r"\((?:current|selected|default|recommended|active)\)"
    r"|\[[^\]]*\]"
    r"|[✓✔✗✘●◉]"
    r"|\((?:thinking|reasoning[^)]*)\)"
    r")\s*",
    re.IGNORECASE,
)
# Lines that are purely box-drawing / rule separators (no real text).
_SEPARATOR_CHARS = set("─━│┃╭╮╰╯┌┐└┘├┤┬┴┼╎╏╌╍┄┅┈┉▄▀▔▁▂▃▅▆▇█▌▐═║╔╗╚╝-=_")
# Spinner / status lines a harness paints over its own output region.
_SPINNER_LINE_RE = re.compile(
    r"^\s*[✻✶✳✽✢⠁-⣿◐◓◑◒↻⟳]+\s+\w+",
)
_UI_CHROME_RE = re.compile(
    r"""
    ^\s*(?:
        Composer\s+\d+\b.*(?:Auto-run|files?\s+edited|%)
        |ctrl\+r\s+to\s+review\s+edits\b.*
        |Auto-run\s*$
        |\u2192\s*Add\s+a\s+follow-up\s*$
        |\u256d.*\u256e\s*$
        |\u2570.*\u256f\s*$
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def strip_ui_chrome(s: str) -> str:
    """Remove known harness status/footer lines before sentinel parsing."""
    clean = strip_ansi(s)
    return "\n".join(
        line for line in clean.splitlines() if not _UI_CHROME_RE.match(line.strip())
    )


def _model_label(model_id: str) -> str:
    leaf = model_id.rsplit("/", 1)[-1]
    return leaf.replace("-", " ").replace("_", " ").title()


def _clean_model_line(line: str) -> str:
    clean = strip_ansi(line).strip().strip("│┃║╎╏╭╮╰╯")
    clean = _LIST_LEADER_RE.sub("", clean)
    clean = _LIST_NUMBER_RE.sub("", clean)
    clean = _MODEL_TAG_RE.sub(" ", clean)
    clean = re.sub(r"\b(current|selected)\b", "", clean, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", clean).strip(" -:|·•")


_VERSION_BANNER_RE = re.compile(r"v\d+(?:\.\d+){1,}(?:-[\w.]+)?", re.IGNORECASE)
_MODEL_FILE_SUFFIXES = (".gguf", ".bin", ".safetensors")
_MAX_MODEL_LABEL_LEN = 72
_MIN_RULE_LEN = 3
# Lines we never treat as a model row (UI labels, prompts, prose).
_MODEL_SKIP_FRAGMENTS = (
    "/model",
    "select a model",
    "select model",
    "available models",
    "choose a model",
    "model scope",
    "model name:",
    "model to change",
    "openai codex",
    "cursor agent",
    "access legacy",
    "running codex",
    "in your config",
    "switch between",
    "applies to this session",
    "press enter",
    "use arrow",
    "type to filter",
    "esc to",
    "enter to",
    "tab to",
    "tab scope",
    "scope:",
    "to confirm",
    "to change",
    "to adjust",
    "ctrl+",
    "%/",
    "update available",
    "new version",
)


def _model_id_from_token(token: str) -> str | None:
    """Return ``token`` if it looks like a usable model identifier, else None.

    Accepts ``provider/model`` slugs (lowercase provider, so a filesystem path
    like ``Agents/projects`` is rejected), local weight filenames, and the
    common ``letter…digit`` shape (``gpt-5.5``, ``claude-sonnet-4-6``,
    ``Qwen3.6-35B``). Rejects bare numbers, ``200K``-style metadata, and CLI
    version banners (``v0.71.0``, ``v2026.05.09-abc``).
    """
    if _VERSION_BANNER_RE.fullmatch(token):
        return None
    if "/" in token:
        provider = token.split("/", 1)[0]
        if provider and provider[0].isalnum() and not any(c.isupper() for c in provider):
            return token
        return None
    if token.lower().endswith(_MODEL_FILE_SUFFIXES):
        return token
    digit_pos = next((i for i, c in enumerate(token) if c.isdigit()), -1)
    letter_pos = next((i for i, c in enumerate(token) if c.isalpha()), -1)
    if digit_pos != -1 and letter_pos != -1 and letter_pos < digit_pos:
        return token
    return None


def parse_harness_model_list(pane_text: str) -> list[tuple[str, str]]:
    """Extract model choices from a harness `/model(s)` pane capture.

    Harness model pickers vary wildly (numbered codex list, claude radio
    dialog, cursor table, pi ``provider/model`` list). This is a conservative
    heuristic: it keeps an ordered ``(model_id, label)`` list, drawing the id
    from a backtick/parenthesised hint or the first model-id-shaped token on
    the line, and skips obvious chrome. Adapters whose picker doesn't survive
    this (or who have a stable hardcoded list) should set
    ``model_list_command = None`` rather than feed it garbage.
    """
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()

    for raw_line in strip_ui_chrome(pane_text).splitlines():
        line = _clean_model_line(raw_line)
        if not line:
            continue
        if line.startswith(("~/", "/", "./", "../")):
            continue  # a filesystem path (cwd banner), not a model
        lowered = line.lower()
        if any(fragment in lowered for fragment in _MODEL_SKIP_FRAGMENTS):
            continue
        if set(line) <= _SEPARATOR_CHARS | {" "}:
            continue

        model_id = ""
        if code_match := _CODE_SPAN_RE.search(line):
            model_id = code_match.group(1).strip()
        elif paren_match := _PAREN_ID_RE.search(line):
            model_id = paren_match.group(1).strip()
        else:
            for token in (m.group(0) for m in _MODEL_ID_RE.finditer(line)):
                candidate = _model_id_from_token(token)
                if candidate:
                    model_id = candidate
                    break
        if model_id and _VERSION_BANNER_RE.fullmatch(model_id):
            model_id = ""

        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        keep_line = 0 < len(line) <= _MAX_MODEL_LABEL_LEN and line != model_id
        rows.append((model_id, line if keep_line else _model_label(model_id)))

    return rows


_TOOL_GLYPH_RE = re.compile(r"^\s*[⏺⎿└╰├│•·⤷●○◦‣▪▸]+\s*")
_SLASH_COMMAND_RE = re.compile(r"/[A-Za-z][\w-]*\Z")


def _clean_assistant_line(line: str) -> str:
    """Strip a leading tool-call / continuation glyph (⏺ ⎿ ● • │ …) from a line.

    These are pure harness chrome around tool output; removing the prefix
    leaves the actual text without disturbing real indentation.
    """
    return _TOOL_GLYPH_RE.sub("", line.rstrip())


def is_rule_line(line: str) -> bool:
    """True for a line that is only box-drawing / horizontal-rule characters."""
    s = line.strip().replace(" ", "")
    return len(s) >= _MIN_RULE_LEN and set(s) <= _SEPARATOR_CHARS


def is_status_spinner_line(line: str) -> bool:
    """True for a harness's own progress/spinner line (``✻ Brewed for 7s`` …)."""
    return bool(_SPINNER_LINE_RE.match(line))


def parse_prompt_marker_transcript(
    pane_text: str,
    *,
    prompt_markers: tuple[str, ...],
    drop_substrings: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    """Generic CLI-harness transcript parser.

    A line whose stripped form is exactly one of ``prompt_markers`` (``>``,
    ``❯``, ``›`` …) or begins with one followed by a space is taken as the
    user's submitted prompt; the block of lines until the next such prompt is
    that turn's assistant/tool output. Lines before the first prompt (banner,
    MOTD) are dropped; lines containing any of ``drop_substrings`` (status
    bars), horizontal rules, and the harness's own spinner lines are dropped;
    bare prompts, prompts that are just a slash-command echo, and a dangling
    final prompt with no reply yet (the live input box) are not emitted.

    Returns ``(role, text)`` turns with ``role`` in ``{"user", "assistant"}``,
    or ``[]`` if no prompt line is visible. This is a heuristic keyed to the
    common "echoed prompt + free-text reply" shape — adapters whose UI has
    cleaner structure should override ``HarnessAdapter.parse_transcript`` with
    something tighter, ideally fixture-tested against a real pane capture.
    """
    if not prompt_markers:
        return []

    lines = strip_ansi(pane_text).splitlines()
    lowered_drops = tuple(d.lower() for d in drop_substrings)

    def split_prompt(line: str) -> tuple[bool, str]:
        s = line.strip()
        for marker in prompt_markers:
            if s == marker:
                return True, ""
            if s.startswith(marker + " "):
                return True, s[len(marker) + 1 :].strip()
        return False, ""

    def is_chrome(line: str) -> bool:
        s = line.strip()
        if not s:
            return False
        if is_rule_line(line) or is_status_spinner_line(line):
            return True
        lowered = s.lower()
        return any(d in lowered for d in lowered_drops)

    turns: list[tuple[str, str]] = []
    cur_user: str | None = None
    assistant_lines: list[str] = []
    seen_prompt = False

    def flush(*, final: bool = False) -> None:
        if cur_user is None:
            return
        body = "\n".join(assistant_lines).strip()
        if final and not body:
            return  # dangling prompt with no reply yet → the live input box
        turns.append(("user", cur_user))
        if body:
            turns.append(("assistant", body))

    for line in lines:
        if is_chrome(line):
            continue
        is_prompt, prompt_text = split_prompt(line)
        if is_prompt:
            flush()
            seen_prompt = True
            # Bare prompt or a slash-command echo (`/model`, `/clear`) → noise.
            is_noise = not prompt_text or _SLASH_COMMAND_RE.fullmatch(prompt_text)
            cur_user = None if is_noise else prompt_text
            assistant_lines = []
            continue
        if not seen_prompt:
            continue
        assistant_lines.append(_clean_assistant_line(line))

    flush(final=True)
    return turns


def extract_last_message_heuristic(pane_text: str, *, max_lines: int = 40) -> str | None:
    lines = [ln.rstrip() for ln in strip_ansi(pane_text).splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines:
        return None
    block: list[str] = []
    for ln in reversed(lines[-max_lines:]):
        s = ln.strip()
        if not s:
            if block:
                break
            continue
        if s in (">", "$", "%", "#") or (len(s) == 1 and s in ">#$%"):
            if block:
                break
            continue
        block.append(ln)
    if not block:
        return None
    block.reverse()
    return "\n".join(block).strip() or None
