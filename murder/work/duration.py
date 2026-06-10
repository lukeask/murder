"""Shared duration-string parser for schedule input.

Parses compact duration strings made of ``d`` (days), ``h`` (hours) and
``m`` (minutes) components into a :class:`datetime.timedelta`. Callers add the
result to ``now()`` to produce a ``schedule_at`` timestamp; this util is pure
and does no clock work itself.

Accepted forms (units must appear at most once, in ``d h m`` order):
``"1d4h3m"``, ``"1h1m"``, ``"34m"``, ``"1h"``, ``"2d"``.

Malformed input raises :class:`ValueError` (the Python scalar-parse convention,
e.g. ``int("garbage")``). Malformed includes: empty/whitespace, a bare number
with no unit (``"34"``), unknown units (``"5w"``), negative values (``"-1h"``),
out-of-order or duplicated units (``"3m1h"``, ``"1h1h"``), and any garbage.
"""

from __future__ import annotations

import re
from datetime import timedelta

__all__ = ["parse_duration"]

# Anchored: each unit optional but in fixed d/h/m order, appearing at most once.
# A bare integer has no unit suffix and therefore will not fullmatch.
_DURATION_RE = re.compile(
    r"(?:(?P<days>\d+)d)?(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?",
)


def parse_duration(text: str) -> timedelta:
    """Parse a compact duration string into a :class:`timedelta`.

    See the module docstring for accepted forms. Raises :class:`ValueError`
    on any malformed input, including the empty string (which would otherwise
    match the all-optional regex and silently yield ``timedelta(0)``).
    """
    if not isinstance(text, str):
        raise ValueError(f"duration must be a string, got {type(text).__name__}")

    candidate = text.strip()
    if not candidate:
        raise ValueError("duration string is empty")

    match = _DURATION_RE.fullmatch(candidate)
    if match is None:
        raise ValueError(f"invalid duration: {text!r}")

    parts = match.groupdict()
    # All-optional regex matches a string with no recognised units (e.g. it
    # could only have reached here as the empty match, already guarded above,
    # but stay defensive).
    if not any(parts.values()):
        raise ValueError(f"invalid duration: {text!r}")

    return timedelta(
        days=int(parts["days"] or 0),
        hours=int(parts["hours"] or 0),
        minutes=int(parts["minutes"] or 0),
    )
