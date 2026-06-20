"""Detect the planner's YAML carving form in a pane/transcript.

The planner emits a carve form in chat immediately after writing a ticket `.md`
(see ``prompts/planner.md``)::

    ```yaml
    id: t014
    title: short title
    write_set:
      - path/to/file.py
    deps: []
    harness_override:
    checklist:
      - a verifiable done-criterion
    ```

Nothing scanned the pane for this form, so a `planned` ticket never reached
`ready`. This module mirrors ``HarnessAdapter.detect_answers``: it strips UI
chrome, finds carve-form YAML blocks, and returns the ones that look like a
carve form (a mapping carrying an ``id``). The scanner is intentionally NOT in
``llm/harnesses/base.py`` (owned elsewhere); the planning path imports it here.

Live planners (claude_code/opus in particular) frequently emit the carve form
as *bare indented YAML* rather than inside a ```yaml fence. The scanner is
therefore tolerant of BOTH shapes: it first reaps every fenced block, then
sweeps the remaining text for unfenced indented carve forms detected by their
shape (an ``id:`` line accompanied by ``title:`` and ``write_set:``). The fenced
path is unchanged and remains authoritative; the unfenced sweep only fills the
gaps the fences didn't already cover.
"""

from __future__ import annotations

import re
import textwrap
from typing import Any

import yaml

from murder.llm.harnesses.parsing import strip_ui_chrome

# Fenced ```yaml ... ``` block. The info-string may be `yaml` or `yml`; tolerate
# trailing whitespace and a missing language tag is NOT matched (a bare ``` block
# is too ambiguous to treat as a carve form).
_YAML_FENCE_RE = re.compile(
    r"```(?:yaml|yml)\s*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)

# Head of an *unfenced* carve form: a top-of-block ``id:`` mapping key with a
# non-empty scalar value. The leading-whitespace group is captured so we can
# bound the block by indentation (the form's body is indented >= the ``id:``
# line). A YAML comment after the value (``# e.g. t014``) is tolerated by the
# parser, so we don't strip it here.
_CARVE_ID_LINE_RE = re.compile(r"^(?P<indent>[ \t]*)id:[ \t]+\S.*$")

# Sibling keys that, together with ``id:``, identify the carve form's shape.
_CARVE_SHAPE_KEYS = ("title:", "write_set:")


def _looks_like_carve_block(block: str) -> bool:
    """True iff ``block`` carries the carve-form shape (id + title + write_set)."""
    return all(re.search(rf"(?m)^[ \t]*{re.escape(k)}", block) for k in _CARVE_SHAPE_KEYS)


def _parse_carve_block(body: str, forms: list[dict[str, Any]]) -> bool:
    """Parse one candidate block; append to ``forms`` if it is a carve form.

    Returns True iff a form was appended.
    """
    try:
        loaded = yaml.safe_load(body)
    except yaml.YAMLError:
        return False
    if not isinstance(loaded, dict):
        return False
    ticket_id = loaded.get("id")
    if not isinstance(ticket_id, str) or not ticket_id.strip():
        return False
    spec = {str(k): v for k, v in loaded.items()}
    spec["id"] = ticket_id.strip()
    forms.append(spec)
    return True


def _iter_unfenced_blocks(text: str) -> "list[str]":
    """Yield dedented candidate YAML blocks for each unfenced carve-form head.

    The block starts at an ``id:`` line and extends through every following line
    that belongs to the same indented mapping. The block ENDS at the first line
    that is *less* indented than the ``id:`` line (a dedent), a fresh
    less-or-equally-indented ``id:`` line (the next form's head), or a blank line
    that is followed by a dedent (a true paragraph break — a blank line still
    inside the indented block, e.g. between mapping keys, is kept).
    """
    lines = text.splitlines()
    blocks: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        head = _CARVE_ID_LINE_RE.match(lines[i])
        if head is None:
            i += 1
            continue
        base_indent = len(head.group("indent").expandtabs(4))
        block_lines = [lines[i]]
        j = i + 1
        while j < n:
            line = lines[j]
            if line.strip() == "":
                # A blank line ends the block only if what follows dedents out of
                # (or breaks) the indented block. Otherwise it's an interior gap.
                k = j + 1
                while k < n and lines[k].strip() == "":
                    k += 1
                if k >= n:
                    break
                nxt = lines[k]
                nxt_indent = len(nxt[: len(nxt) - len(nxt.lstrip())].expandtabs(4))
                if nxt_indent <= base_indent and not nxt.lstrip().startswith("- "):
                    break
                block_lines.append(line)
                j += 1
                continue
            cur_indent = len(line[: len(line) - len(line.lstrip())].expandtabs(4))
            if cur_indent < base_indent:
                break  # dedent out of the block
            if cur_indent == base_indent and _CARVE_ID_LINE_RE.match(line):
                break  # next form's head at the same level
            block_lines.append(line)
            j += 1
        blocks.append(textwrap.dedent("\n".join(block_lines)))
        i = j
    return blocks


def detect_carve_forms(pane_text: str) -> list[dict[str, Any]]:
    """Return parsed carve-form mappings found in ``pane_text``.

    A block qualifies as a carve form when it parses to a mapping with a
    non-empty string ``id``. Both fenced (```yaml) and bare indented forms are
    accepted; malformed YAML / non-mapping / id-less blocks are skipped silently
    (the pane is noisy; only real carve forms are returned). Order follows
    appearance in the pane.
    """
    clean = strip_ui_chrome(pane_text)
    forms: list[dict[str, Any]] = []

    # 1. Fenced ```yaml blocks (authoritative, unchanged). Blank out each matched
    #    span so the unfenced sweep below can't re-detect the same form.
    leftover_parts: list[str] = []
    last = 0
    for match in _YAML_FENCE_RE.finditer(clean):
        _parse_carve_block(match.group("body"), forms)
        leftover_parts.append(clean[last : match.start()])
        # Preserve line count by replacing the fence span with its newlines only.
        leftover_parts.append("\n" * match.group(0).count("\n"))
        last = match.end()
    leftover_parts.append(clean[last:])
    leftover = "".join(leftover_parts)

    # 2. Unfenced indented carve forms, detected by shape.
    for body in _iter_unfenced_blocks(leftover):
        if _looks_like_carve_block(body):
            _parse_carve_block(body, forms)

    return forms
