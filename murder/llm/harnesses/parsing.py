from __future__ import annotations

import re
from collections.abc import Callable

from murder.llm.harnesses.models import HarnessEffortChoice, HarnessModelChoice

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
# The braille run starts at U+2800 (BRAILLE BLANK), not U+2801: Cursor animates
# its spinner with frames like "⠀⠞ Editing  9.67k tokens" whose leading cell is
# the blank glyph, so excluding U+2800 let those frames leak into the transcript.
_SPINNER_LINE_RE = re.compile(
    r"^\s*[✻✶✳✽✢⠀-⣿◐◓◑◒↻⟳]+\s+\w+",
)
# CC 2.x thinking-status spinner: "* Recombobulating.. (8m 11s • ↓ 24.1k tokens)"
# The leading * is animated (sometimes absent); anchor on the stable part: a
# capitalised verb followed by 2+ dots and a parenthetical containing "tokens".
_CC_THINKING_STATUS_RE = re.compile(
    r"^\s*\*?\s*[A-Z]\w+\.{2,}\s*\([^)]*tokens",
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
    return "\n".join(line for line in clean.splitlines() if not _UI_CHROME_RE.match(line.strip()))


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
_MODEL_STATUS_PATH_RE = re.compile(r"\s·\s(?:~/|/|\./|\.\./)")
_MODEL_PROSE_URL_RE = re.compile(r"(?:https?://|www\.|discord\.gg/)", re.IGNORECASE)
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
    "extended keys",
)

_NUMBERED_ROW_RE = re.compile(
    r"^[\s>›→▸▶❯»*•·●○◉◆◇■□☑☒✓✔✗✘\-\u00a0]*"
    r"(?P<index>\d{1,3})[.)\]]\s+"
    r"(?P<body>.+?)\s*$"
)
_CURRENT_MARKER_RE = re.compile(
    r"\b(current|selected|active)\b|[✓✔]",
    re.IGNORECASE,
)
_EFFORT_WORD_RE = re.compile(
    r"\b(?:extra\s+high|x\s*high|xhigh|max|high|medium|low)\b",
    re.IGNORECASE,
)

_EFFORT_ALIASES = {
    "low": "low",
    "medium": "medium",
    "med": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "x-high": "xhigh",
    "x high": "xhigh",
    "extrahigh": "xhigh",
    "extra high": "xhigh",
    "extra-high": "xhigh",
    "max": "max",
    "slow": "slow",
    "fast": "fast",
}
_POINTED_ROW_RE = re.compile(
    r"^\s*"
    r"(?P<point>[>→])\s+"
    r"(?P<body>.+?)\s*$"
)
_AGY_EFFORT_IN_LABEL_RE = re.compile(
    r"\((Low|Medium|High|Thinking)\)",
    re.IGNORECASE,
)
_CURSOR_MODEL_PAGE_RE = re.compile(
    r"(?P<start>\d+)\s*-\s*(?P<end>\d+)\s+of\s+(?P<total>\d+)",
    re.IGNORECASE,
)
_CURSOR_MODEL_MENU_SKIP_RE = re.compile(
    r"^(?:available models|filter:?\s*$|type to filter|\d+\s*-\s*\d+\s+of\s+\d+)",
    re.IGNORECASE,
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


def normalize_effort(value: str | None) -> str | None:
    """Normalize harness effort/reasoning labels to stable config values."""
    if value is None:
        return None
    raw = re.sub(r"\s+", " ", value.strip().lower().replace("_", " "))
    if not raw:
        return None
    compact = raw.replace(" ", "").replace("-", "")
    return _EFFORT_ALIASES.get(raw) or _EFFORT_ALIASES.get(compact)


def parse_numbered_model_choices(pane_text: str) -> list[HarnessModelChoice]:
    """Parse numbered model picker rows while preserving row indices."""
    choices: list[HarnessModelChoice] = []
    seen: set[tuple[int | None, str]] = set()
    for raw_line in strip_ui_chrome(pane_text).splitlines():
        raw = strip_ansi(raw_line).rstrip()
        match = _NUMBERED_ROW_RE.match(raw)
        if match is None:
            continue
        index = int(match.group("index"))
        body = match.group("body")
        parsed = parse_harness_model_list(raw)
        if parsed:
            model_id, label = parsed[0]
        else:
            body_clean = _clean_model_line(raw)
            first = body_clean.split(maxsplit=1)[0] if body_clean else ""
            if not first or not any(ch.isalpha() for ch in first):
                continue
            model_id = first
            label = body_clean
        key = (index, model_id)
        if key in seen:
            continue
        seen.add(key)
        choices.append(
            HarnessModelChoice(
                index=index,
                model_id=model_id,
                label=label,
                current=bool(_CURRENT_MARKER_RE.search(body)),
            )
        )
    return choices


def parse_numbered_effort_choices(pane_text: str) -> list[HarnessEffortChoice]:
    """Parse numbered effort/reasoning rows from model-selection panes."""
    choices: list[HarnessEffortChoice] = []
    seen: set[tuple[int | None, str]] = set()
    for raw_line in strip_ui_chrome(pane_text).splitlines():
        raw = strip_ansi(raw_line).rstrip()
        match = _NUMBERED_ROW_RE.match(raw)
        if match is None:
            continue
        index = int(match.group("index"))
        body = match.group("body")
        effort_match = _EFFORT_WORD_RE.search(body)
        effort = normalize_effort(effort_match.group(0) if effort_match else None)
        if effort is None:
            continue
        key = (index, effort)
        if key in seen:
            continue
        seen.add(key)
        choices.append(
            HarnessEffortChoice(
                index=index,
                effort=effort,
                label=re.sub(r"\s+", " ", body).strip(),
                current=bool(_CURRENT_MARKER_RE.search(body)),
            )
        )
    return choices


# Claude Code's `/model` radio dialog renders one numbered row per model the
# harness presents. Each row is `<label>  <description>`; the slash-command id
# (`/model <id>`) is derived from the *label*, not a coarse family match — so
# distinct rows that share a family (e.g. plain Sonnet vs `Sonnet (1M context)`,
# or Opus vs Opus Plan Mode) keep distinct ids instead of collapsing. The id set
# is whatever the live menu presents; we never hardcode an allowlist of which
# models are "real".
#
# Confirmed against Claude Code v2.1.172's live `/model` menu + `/model <id>`
# round-trips (2026-06-10): Default→`default`, `Sonnet (1M context)`→`sonnet[1m]`,
# Fable→`fable`, Opus→`opus`, Haiku→`haiku`. Opus Plan Mode (`opusplan`) is
# handled for harness versions that present it though it wasn't in this capture.
def _claude_code_slash_id(label: str) -> str | None:
    """Derive the `/model <id>` slash arg from a Claude Code menu row label.

    ``label`` is the leading row text (before the long description). Returns the
    slash id Claude Code's `/model` command accepts, or ``None`` for rows that
    aren't selectable models (e.g. the ``Custom model`` reflection row).
    """
    text = re.sub(r"\s+", " ", label).strip().rstrip("·-:").strip().lower()
    if not text:
        return None
    # "Default (recommended)" → default.
    if text.startswith("default"):
        return "default"
    # "Sonnet (1M context)" / "Sonnet 1M" → sonnet[1m]; plain Sonnet → sonnet.
    if "sonnet" in text:
        if re.search(r"\b1m\b|1m\s*context|\[1m\]", text):
            return "sonnet[1m]"
        return "sonnet"
    # "Opus Plan Mode" / "Opus (plan)" → opusplan; plain Opus → opus.
    if "opus" in text:
        if "plan" in text:
            return "opusplan"
        return "opus"
    if "haiku" in text:
        return "haiku"
    if "fable" in text:
        return "fable"
    return None


def parse_claude_code_model_choices(pane_text: str) -> list[HarnessModelChoice]:
    """Parse every model row Claude Code's `/model` radio dialog presents.

    Keeps one entry per numbered row, deriving the `/model <id>` slash arg from
    the row label (so same-family variants stay distinct) and deduping by that
    id. Robust to leading selection ornaments / trailing ``✔`` markers / box
    chrome via the shared strip helpers. The current/selected row (marked by a
    leading ``❯``/``>`` pointer or a ``✓``/``current`` marker) is flagged.
    """
    rows: list[HarnessModelChoice] = []
    seen: set[str] = set()
    for raw_line in strip_ui_chrome(pane_text).splitlines():
        raw = strip_ansi(raw_line).rstrip()
        match = _NUMBERED_ROW_RE.match(raw)
        if match is None:
            continue
        body = re.sub(r"\s+", " ", match.group("body")).strip()
        # The row is `<label>  <description>`; split on the first run of 2+
        # spaces in the *raw* body to isolate the label the id is derived from.
        raw_body = match.group("body")
        segments = re.split(r"\s{2,}", raw_body.strip(), maxsplit=1)
        label_part = segments[0].strip()
        description = segments[1].strip() if len(segments) > 1 else ""
        # The "Custom model" row is Claude Code echoing a `--model <alias>`
        # override, not one of the menu's offered models — skip it (the alias it
        # reflects already maps onto a presented row, or onto the same id).
        if description.lower().startswith("custom model"):
            continue
        # Strip the trailing selection marker (`✔`/`✓`) from the label text.
        label_part = re.sub(r"[✓✔]", "", label_part).strip()
        model_id = _claude_code_slash_id(label_part)
        if model_id is None or model_id in seen:
            continue
        seen.add(model_id)
        rows.append(
            HarnessModelChoice(
                index=int(match.group("index")),
                model_id=model_id,
                label=body,
                current=bool(_CURRENT_MARKER_RE.search(body) or raw.lstrip().startswith((">", "❯"))),
            )
        )
    return rows


def parse_cursor_model_page(pane_text: str) -> tuple[int, int, int] | None:
    """Return ``(start, end, total)`` from Cursor's ``1-10 of 27`` picker footer."""
    clean = strip_ansi(pane_text)
    matches = list(_CURSOR_MODEL_PAGE_RE.finditer(clean))
    if not matches:
        return None
    match = matches[-1]
    return (
        int(match.group("start")),
        int(match.group("end")),
        int(match.group("total")),
    )


def parse_cursor_model_list(
    pane_text: str,
    model_id_for_label: Callable[[str], str | None],
) -> list[tuple[str, str]]:
    """Parse every model row visible on the current Cursor ``/model`` page."""
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    in_menu = False
    for raw_line in strip_ui_chrome(pane_text).splitlines():
        raw = strip_ansi(raw_line).rstrip()
        if not raw or raw.startswith("#"):
            continue
        if "available models" in raw.lower():
            in_menu = True
            continue
        if not in_menu:
            continue
        if _CURSOR_MODEL_MENU_SKIP_RE.match(raw.strip()):
            continue
        if set(raw.strip()) <= _SEPARATOR_CHARS | {" "}:
            continue
        body = re.sub(r"^\s*[>→]\s+", "", raw).strip()
        body = re.sub(r"\s*\(Tab to modify\)\s*$", "", body, flags=re.IGNORECASE).strip()
        body = re.sub(r"\s{2,}.*$", "", body).strip()
        if not body:
            continue
        model_id = model_id_for_label(body)
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        rows.append((model_id, body))
    return rows


def slug_model_label(label: str) -> str:
    """Stable lowercase id from a human model label (``Gemini 3.1 Pro`` → ``gemini-3-1-pro``)."""
    base = re.sub(r"\s*\([^)]+\)\s*", " ", label).strip()
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")


def parse_pointed_model_choices(
    pane_text: str,
    *,
    model_id_for_label: Callable[[str], str | None] | None = None,
) -> list[HarnessModelChoice]:
    """Parse arrow-selected model rows (``> …`` / ``→ …``) from picker panes."""
    choices: list[HarnessModelChoice] = []
    seen: set[str] = set()
    for raw_line in strip_ui_chrome(pane_text).splitlines():
        raw = strip_ansi(raw_line).rstrip()
        match = _POINTED_ROW_RE.match(raw)
        if match is None:
            continue
        body = re.sub(r"\s+", " ", match.group("body")).strip()
        if not body or "tab to modify" in body.lower():
            continue
        if re.search(r"plan,\s*search|add a follow-up", body, re.IGNORECASE):
            continue
        if model_id_for_label is not None:
            model_id = model_id_for_label(body)
        else:
            parsed = parse_harness_model_list(body)
            model_id = parsed[0][0] if parsed else slug_model_label(body)
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        choices.append(
            HarnessModelChoice(
                index=None,
                model_id=model_id,
                label=body,
                current=True,
            )
        )
    if choices:
        return choices
    # Pi lists every row but only marks the current one with →.
    for raw_line in strip_ui_chrome(pane_text).splitlines():
        raw = strip_ansi(raw_line).rstrip()
        if not raw.startswith(("→", ">")):
            continue
        body = re.sub(r"^[>→]\s+", "", raw).strip()
        parsed = parse_harness_model_list(body)
        if not parsed:
            continue
        model_id, label = parsed[0]
        if model_id in seen:
            continue
        seen.add(model_id)
        choices.append(
            HarnessModelChoice(
                index=None,
                model_id=model_id,
                label=label,
                current=True,
            )
        )
    return choices


def parse_antigravity_model_choices(pane_text: str) -> list[HarnessModelChoice]:
    """Parse Antigravity ``/model`` rows; effort is the trailing ``(Low)`` tag."""
    choices: list[HarnessModelChoice] = []
    seen: set[tuple[str, str | None]] = set()
    in_menu = False
    for raw_line in strip_ui_chrome(pane_text).splitlines():
        raw = strip_ansi(raw_line).rstrip()
        if not raw or raw.startswith("#"):
            continue
        lowered = raw.lower()
        if "switch model" in lowered:
            in_menu = True
            continue
        if not in_menu:
            continue
        if lowered.startswith("keyboard:"):
            break
        if not re.match(r"^\s*>?\s*(?:Gemini|Claude|GPT|Sonnet|Opus)\b", raw, re.IGNORECASE):
            continue
        pointed = _POINTED_ROW_RE.match(raw)
        body = pointed.group("body") if pointed else re.sub(r"^\s*>\s+", "", raw).strip()
        body = re.sub(r"\s+", " ", body).strip()
        if not body:
            continue
        effort_match = _AGY_EFFORT_IN_LABEL_RE.search(body)
        effort = normalize_effort(effort_match.group(1)) if effort_match else None
        base_label = _AGY_EFFORT_IN_LABEL_RE.sub("", body).strip()
        base_label = re.sub(r"\s*\(current\)\s*", "", base_label, flags=re.IGNORECASE).strip()
        model_id = slug_model_label(base_label)
        if not model_id:
            continue
        key = (model_id, effort)
        if key in seen:
            continue
        seen.add(key)
        choices.append(
            HarnessModelChoice(
                index=None,
                model_id=model_id,
                label=body,
                current=bool(pointed) or "(current)" in body.lower(),
            )
        )
    return choices


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
        if _MODEL_STATUS_PATH_RE.search(line):
            continue  # status/footer line with cwd, not a model choice
        if _MODEL_PROSE_URL_RE.search(line):
            continue  # prose/tip line carrying a URL, not a model choice
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


def is_rule_line(line: str) -> bool:
    """True for a line that is only box-drawing / horizontal-rule characters."""
    s = line.strip().replace(" ", "")
    return len(s) >= _MIN_RULE_LEN and set(s) <= _SEPARATOR_CHARS


def is_status_spinner_line(line: str) -> bool:
    """True for a harness's own progress/spinner line (``✻ Brewed for 7s``, ``* Galloping.. (7s · tokens)`` …)."""
    return bool(_SPINNER_LINE_RE.match(line) or _CC_THINKING_STATUS_RE.match(line))


def is_tool_glyph_line(line: str) -> bool:
    """True for a line starting with a tool-call / continuation glyph (``⏺`` ``⎿`` ``●`` …)."""
    return bool(_TOOL_GLYPH_RE.match(line.strip()))


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
