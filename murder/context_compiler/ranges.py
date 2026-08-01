"""Pure inclusive line-range normalization, subtraction, and clamping.

All endpoints are one-based and inclusive at both ends.

Clamping policy
---------------
* Ranges with ``start_line > line_count`` are wholly invalid and rejected.
* Ranges that begin within the file but extend past ``line_count`` are clamped
  to ``end_line = line_count``.
* Empty files (``line_count == 0``) reject every positive range.
"""

from __future__ import annotations

from collections.abc import Sequence

from murder.context_compiler.models import LineRange


class RangeValidationError(ValueError):
    """Raised when a requested range cannot be satisfied against current source."""


def normalize_ranges(ranges: Sequence[LineRange]) -> tuple[LineRange, ...]:
    """Sort, merge overlaps, and merge directly adjacent ranges.

    Example: ``23-40, 35-55, 56-60, 80-90`` → ``23-60, 80-90``.
    """
    if not ranges:
        return ()
    ordered = sorted(ranges, key=lambda r: (r.start_line, r.end_line))
    merged: list[LineRange] = [ordered[0]]
    for current in ordered[1:]:
        previous = merged[-1]
        # Overlap or direct adjacency (inclusive): previous.end + 1 >= current.start
        if current.start_line <= previous.end_line + 1:
            if current.end_line > previous.end_line:
                merged[-1] = LineRange(previous.start_line, current.end_line)
        else:
            merged.append(current)
    return tuple(merged)


def subtract_ranges(
    requested: Sequence[LineRange],
    known: Sequence[LineRange],
) -> tuple[LineRange, ...]:
    """Subtract already-known unchanged intervals from requested intervals.

    Example: requested ``23-97``, known ``54-90`` → ``23-53, 91-97``.

    Supports multiple known intervals, nesting, full coverage, disjoint known
    ranges, and requests that cross several known intervals.
    """
    if not requested:
        return ()
    known_normalized = normalize_ranges(known)
    if not known_normalized:
        return normalize_ranges(requested)

    remaining: list[LineRange] = []
    for req in normalize_ranges(requested):
        pieces: list[LineRange] = [req]
        for known_range in known_normalized:
            next_pieces: list[LineRange] = []
            for piece in pieces:
                next_pieces.extend(_subtract_one(piece, known_range))
            pieces = next_pieces
            if not pieces:
                break
        remaining.extend(pieces)
    return normalize_ranges(remaining)


def _subtract_one(requested: LineRange, known: LineRange) -> tuple[LineRange, ...]:
    """Subtract a single known interval from a single requested interval."""
    if known.end_line < requested.start_line or known.start_line > requested.end_line:
        return (requested,)

    left: LineRange | None = None
    right: LineRange | None = None
    if known.start_line > requested.start_line:
        left = LineRange(requested.start_line, known.start_line - 1)
    if known.end_line < requested.end_line:
        right = LineRange(known.end_line + 1, requested.end_line)

    if left is None and right is None:
        return ()
    if left is None:
        assert right is not None
        return (right,)
    if right is None:
        return (left,)
    return (left, right)


def clamp_range(line_range: LineRange, line_count: int) -> LineRange:
    """Validate and optionally clamp a range against a file's line count.

    Wholly invalid selections raise :class:`RangeValidationError`. Partially
    overlong ranges are clamped to ``line_count``.
    """
    if line_count <= 0:
        raise RangeValidationError(
            f"cannot select lines {line_range.start_line}-{line_range.end_line} "
            f"from an empty file (line_count={line_count})"
        )
    if line_range.start_line > line_count:
        raise RangeValidationError(
            f"range {line_range.start_line}-{line_range.end_line} starts beyond "
            f"file end (line_count={line_count})"
        )
    end = min(line_range.end_line, line_count)
    if end == line_range.end_line and end >= line_range.start_line:
        return line_range
    return LineRange(line_range.start_line, end)
