"""Render a ``CorpusProposal`` for the grading model.

Never expose SQL rows, score internals, or rejected graph paths. Oversized
ranges get signature + bounded head/tail + match location + an oversized marker.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from murder.context_compiler.grading.policy import (
    MAX_GRADING_INPUT_CHARS,
    PREVIEW_FULL_SOURCE_TOKEN_CAP,
    PREVIEW_HEAD_LINES,
    PREVIEW_TAIL_LINES,
)
from murder.context_compiler.indexing.queries import (
    find_unit_containing_line,
    list_semantic_units_by_path,
)
from murder.context_compiler.models import ContextRequest, LineRange
from murder.context_compiler.persistence.semantic_units import get_semantic_unit_version
from murder.context_compiler.ranking.models import CorpusProposal, RangeProposal
from murder.context_compiler.ranking.tokens import DEFAULT_TOKEN_COUNTER, TokenCounter
from murder.context_compiler.rendering import RenderError, extract_source_slice
from murder.context_compiler.source import FilesystemSourceReader, SourceReadError

_SUFFIX_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".vue": "vue",
    ".svelte": "svelte",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".html": "html",
    ".htm": "html",
}


def language_for_path(path: str) -> str:
    """Best-effort language label from file suffix."""
    suffix = Path(path).suffix.lower()
    return _SUFFIX_LANGUAGE.get(suffix, "text")


def render_proposal_preview(
    request: ContextRequest,
    proposal: CorpusProposal,
    *,
    worktree_root: Path,
    conn: sqlite3.Connection | None = None,
    token_counter: TokenCounter | None = None,
) -> str:
    """Render the full grading user payload (request header + numbered previews)."""
    counter = token_counter or DEFAULT_TOKEN_COUNTER
    reader = FilesystemSourceReader(worktree_root)
    parts: list[str] = [
        f"objective: {request.objective}",
        f"profile: {request.recipient_profile.value}",
        f"path_hints: {', '.join(request.path_hints) or '(none)'}",
        f"symbol_hints: {', '.join(request.symbol_hints) or '(none)'}",
        # Round-2 expansion merges search_terms / relationship_kind_hints onto
        # the request; surface them so the model sees the same hint set code uses.
        f"search_terms: {', '.join(request.search_terms) or '(none)'}",
        f"relationship_kind_hints: {', '.join(request.relationship_kind_hints) or '(none)'}",
        f"proposal_count: {len(proposal.ranges)}",
        f"proposal_estimated_tokens: {proposal.estimated_tokens}",
        f"truncated: {proposal.truncated}",
        "",
        "proposals:",
    ]
    budget = MAX_GRADING_INPUT_CHARS
    used = sum(len(p) + 1 for p in parts)

    for index, rng in enumerate(proposal.ranges):
        block = _render_one(
            index,
            rng,
            reader=reader,
            conn=conn,
            snapshot_id=proposal.snapshot_id,
            token_counter=counter,
        )
        if used + len(block) + 1 > budget:
            parts.append(
                f"[preview truncated after index {index - 1}; "
                f"{len(proposal.ranges) - index} remaining omitted]"
            )
            break
        parts.append(block)
        used += len(block) + 1

    return "\n".join(parts) + "\n"


def _render_one(
    index: int,
    rng: RangeProposal,
    *,
    reader: FilesystemSourceReader,
    conn: sqlite3.Connection | None,
    snapshot_id: int,
    token_counter: TokenCounter,
) -> str:
    language = language_for_path(rng.path)
    unit_name, unit_kind, signature = _unit_meta(rng, conn=conn, snapshot_id=snapshot_id)
    header = [
        f"[{index}] path={rng.path}",
        f"    lines={rng.line_range.start_line}-{rng.line_range.end_line}",
        f"    language={language}",
        f"    unit_name={unit_name or '(none)'}",
        f"    unit_kind={unit_kind or '(none)'}",
        f"    signature={signature or '(none)'}",
        f"    category={rng.category.value}",
        f"    reasons={', '.join(rng.reasons) or '(none)'}",
        f"    estimated_tokens={rng.estimated_tokens}",
    ]
    try:
        snap = reader.read(rng.path)
        source_block = _source_block(
            snap.text,
            rng.line_range,
            estimated_tokens=rng.estimated_tokens,
            token_counter=token_counter,
            signature=signature,
        )
    except (SourceReadError, RenderError, ValueError) as exc:
        source_block = f"    source=<unreadable: {exc}>"
    return "\n".join([*header, source_block])


def _unit_meta(
    rng: RangeProposal,
    *,
    conn: sqlite3.Connection | None,
    snapshot_id: int,
) -> tuple[str | None, str | None, str | None]:
    if conn is None:
        return None, None, None
    unit = None
    if rng.unit_version_id is not None:
        unit = get_semantic_unit_version(conn, rng.unit_version_id)
    if unit is None:
        unit = find_unit_containing_line(
            conn,
            snapshot_id=snapshot_id,
            relative_path=rng.path,
            line=rng.line_range.start_line,
        )
    if unit is None:
        units = list_semantic_units_by_path(conn, snapshot_id=snapshot_id, relative_path=rng.path)
        for candidate in units:
            if (
                candidate.start_line <= rng.line_range.start_line
                and candidate.end_line >= rng.line_range.end_line
            ):
                unit = candidate
                break
    if unit is None:
        return None, None, None
    return unit.unqualified_name, unit.language_kind, unit.signature


def _source_block(
    text: str,
    line_range: LineRange,
    *,
    estimated_tokens: int,
    token_counter: TokenCounter,
    signature: str | None,
) -> str:
    lines = text.splitlines()
    start = line_range.start_line
    end = line_range.end_line
    if start < 1 or end < start or start > len(lines):
        return "    source=<out of bounds>"

    end = min(end, len(lines))
    span = end - start + 1
    # Prefer measured slice tokens when cheap; fall back to estimate.
    try:
        slice_text = extract_source_slice(text, start, end)
        tokens = token_counter.count_tokens(slice_text)
    except RenderError:
        tokens = estimated_tokens

    full_span_cap = PREVIEW_HEAD_LINES + PREVIEW_TAIL_LINES + 5
    if tokens <= PREVIEW_FULL_SOURCE_TOKEN_CAP and span <= full_span_cap:
        numbered = _number_lines(lines, start, end)
        return "    source:\n" + "\n".join(f"      {row}" for row in numbered)

    # Oversized: signature + head + tail + match + marker.
    head_end = min(end, start + PREVIEW_HEAD_LINES - 1)
    tail_start = max(start, end - PREVIEW_TAIL_LINES + 1)
    parts: list[str] = ["    source: [OVERSIZED]"]
    if signature:
        parts.append(f"      signature: {signature}")
    parts.append("      head:")
    parts.extend(f"        {row}" for row in _number_lines(lines, start, head_end))
    if tail_start > head_end + 1:
        parts.append(f"      … ({tail_start - head_end - 1} lines omitted) …")
        parts.append("      tail:")
        parts.extend(f"        {row}" for row in _number_lines(lines, tail_start, end))
    elif tail_start == head_end + 1:
        parts.append("      tail:")
        parts.extend(f"        {row}" for row in _number_lines(lines, tail_start, end))
    # Match location: midpoint of the proposed range.
    match_line = (start + end) // 2
    if start <= match_line <= end:
        parts.append(f"      match_location: line {match_line}")
        if start <= match_line <= len(lines):
            parts.append(f"        {match_line} {lines[match_line - 1]}")
    return "\n".join(parts)


def _number_lines(lines: list[str], start: int, end: int) -> list[str]:
    out: list[str] = []
    for lineno in range(start, end + 1):
        content = lines[lineno - 1] if lineno <= len(lines) else ""
        out.append(f"{lineno} {content}")
    return out


__all__ = [
    "language_for_path",
    "render_proposal_preview",
]
