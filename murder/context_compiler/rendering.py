"""Recipient-facing rendering for exact source, focused diffs, and deletions.

Source semantic form::

    src/example.py:23-27
    23 def example(...):
    24     value = prepare(...)
    25     if value is None:
    26         return fallback()
    27     return commit(value)

Diff payloads keep the focused-diff text produced by ``build_focused_diff``
(header ``path:old -> path:new`` plus unified body). Deletion notices name the
old path and range only.

Rules:
* source: first line is ``path:start-end``; every line numbered (one-based);
* source whitespace after the line-number prefix is preserved;
* no syntax highlighting, prose synthesis, or whole-file fallback;
* internal IDs (entry_id, delivery_id, content_hash, …) never appear;
* rendered blocks always end with exactly one trailing newline.
"""

from __future__ import annotations

from murder.context_compiler.models import DeletionNotice, EvidenceSegment, PayloadKind


class RenderError(ValueError):
    """Raised when a segment cannot be rendered for the recipient."""


def render_evidence_segment(segment: EvidenceSegment) -> str:
    """Render one segment for the recipient — source excerpt or focused diff."""
    if segment.payload_kind is PayloadKind.SOURCE:
        return render_source_segment(segment)
    if segment.payload_kind is PayloadKind.DIFF:
        return render_diff_segment(segment)
    raise RenderError(f"unsupported payload kind {segment.payload_kind!r}")


def render_source_segment(segment: EvidenceSegment) -> str:
    """Render one ``EvidenceSegment`` as numbered exact-source text."""
    if segment.payload_kind is not PayloadKind.SOURCE:
        raise RenderError(
            f"source renderer does not support payload kind {segment.payload_kind!r}; "
            "use render_evidence_segment for DIFF"
        )
    header = f"{segment.path}:{segment.start_line}-{segment.end_line}"
    lines = segment.payload_text.splitlines()
    expected = segment.end_line - segment.start_line + 1
    if len(lines) != expected:
        raise RenderError(
            f"payload for {segment.path}:{segment.start_line}-{segment.end_line} "
            f"has {len(lines)} lines; expected {expected}"
        )
    numbered = [f"{segment.start_line + offset} {line}" for offset, line in enumerate(lines)]
    return header + "\n" + "\n".join(numbered) + "\n"


def render_diff_segment(segment: EvidenceSegment) -> str:
    """Render a focused-diff segment without inventing prose or internal IDs.

    The ledger already built recipient-facing text; this path only normalizes
    the trailing newline so callers get a stable block.
    """
    if segment.payload_kind is not PayloadKind.DIFF:
        raise RenderError(f"diff renderer does not support payload kind {segment.payload_kind!r}")
    text = segment.payload_text
    if not text:
        raise RenderError(f"empty DIFF payload for {segment.path}")
    return text if text.endswith("\n") else text + "\n"


def render_deletion_notice(notice: DeletionNotice) -> str:
    """Render a deletion notice — path and range only, no internal IDs."""
    return notice.render()


def extract_source_slice(text: str, start_line: int, end_line: int) -> str:
    """Extract an inclusive one-based line slice from decoded source text.

    Preserves per-line content without a trailing newline on the final line.
    Blank lines are preserved as empty strings in the joined result.
    """
    lines = text.splitlines()
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        raise RenderError(
            f"cannot extract lines {start_line}-{end_line} from text with {len(lines)} lines"
        )
    return "\n".join(lines[start_line - 1 : end_line])
