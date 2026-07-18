"""Minimal 5-field cron evaluator (stdlib only).

Supports the common subset: ``*``, ``N``, ``N-M``, ``*/S``, ``N-M/S``, and
comma lists. Day-of-week uses 0–6 with Sunday=0 (7 is also Sunday). When both
day-of-month and day-of-week are constrained, a time matches if either field
matches (Vixie-style).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_FIELD_BOUNDS = (
    (0, 59),  # minute
    (0, 23),  # hour
    (1, 31),  # day of month
    (1, 12),  # month
    (0, 7),  # day of week (0 and 7 = Sunday)
)


def resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown cron timezone {name!r}") from exc


def parse_cron_expression(expression: str) -> tuple[frozenset[int], ...]:
    parts = expression.split()
    if len(parts) != 5:
        raise ValueError(
            f"cron expression must have 5 fields, got {len(parts)}: {expression!r}"
        )
    return tuple(
        _parse_field(part, lo, hi) for part, (lo, hi) in zip(parts, _FIELD_BOUNDS, strict=True)
    )


def iter_cron_fires(
    expression: str,
    *,
    after: datetime,
    until: datetime,
    timezone: str = "UTC",
    limit: int = 500,
) -> tuple[datetime, ...]:
    """Return fire times in ``(after, until]`` as UTC-aware datetimes."""

    if after.tzinfo is None or until.tzinfo is None:
        raise ValueError("cron bounds must be timezone-aware")
    if until <= after or limit <= 0:
        return ()

    tz = resolve_timezone(timezone)
    fields = parse_cron_expression(expression)
    minute_set, hour_set, dom_set, month_set, dow_set = fields
    dom_star = len(dom_set) == 31 and set(range(1, 32)).issubset(dom_set)
    # Dow "star" means every weekday 0-6 (7 aliases Sunday).
    dow_star = set(range(7)).issubset({d % 7 for d in dow_set})

    local_after = after.astimezone(tz)
    local_until = until.astimezone(tz)
    # Start at the next whole minute after ``after``.
    cursor = local_after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    fires: list[datetime] = []
    # Bound scanning so a huge catch-up window cannot spin forever.
    max_steps = max(limit * 60 * 24, 60 * 24 * 8)

    for _ in range(max_steps):
        if cursor > local_until or len(fires) >= limit:
            break
        if (
            cursor.minute in minute_set
            and cursor.hour in hour_set
            and cursor.month in month_set
            and _day_matches(cursor, dom_set, dow_set, dom_star=dom_star, dow_star=dow_star)
        ):
            fires.append(cursor.astimezone(ZoneInfo("UTC")))
        cursor += timedelta(minutes=1)
    return tuple(fires)


def _day_matches(
    value: datetime,
    dom_set: frozenset[int],
    dow_set: frozenset[int],
    *,
    dom_star: bool,
    dow_star: bool,
) -> bool:
    dom_ok = value.day in dom_set
    # datetime.weekday(): Monday=0 … Sunday=6 → cron Sunday=0.
    cron_dow = (value.weekday() + 1) % 7
    dow_ok = cron_dow in dow_set or (cron_dow == 0 and 7 in dow_set)
    if not dom_star and not dow_star:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def _parse_field(field: str, minimum: int, maximum: int) -> frozenset[int]:
    values: set[int] = set()
    for chunk in field.split(","):
        chunk = chunk.strip()
        if not chunk:
            raise ValueError(f"empty cron field segment in {field!r}")
        if chunk.startswith("*/"):
            step = _int(chunk[2:], field)
            if step <= 0:
                raise ValueError(f"cron step must be positive in {field!r}")
            values.update(range(minimum, maximum + 1, step))
            continue
        if "/" in chunk:
            base, step_text = chunk.split("/", 1)
            step = _int(step_text, field)
            if step <= 0:
                raise ValueError(f"cron step must be positive in {field!r}")
            start, end = _range(base, minimum, maximum, field)
            values.update(range(start, end + 1, step))
            continue
        if chunk == "*":
            values.update(range(minimum, maximum + 1))
            continue
        if "-" in chunk:
            start, end = _range(chunk, minimum, maximum, field)
            values.update(range(start, end + 1))
            continue
        number = _int(chunk, field)
        if number < minimum or number > maximum:
            raise ValueError(f"cron value {number} out of range [{minimum}, {maximum}]")
        values.add(number)
    return frozenset(values)


def _range(text: str, minimum: int, maximum: int, field: str) -> tuple[int, int]:
    if text == "*":
        return minimum, maximum
    if "-" not in text:
        value = _int(text, field)
        return value, value
    left, right = text.split("-", 1)
    start = _int(left, field)
    end = _int(right, field)
    if start > end or start < minimum or end > maximum:
        raise ValueError(f"invalid cron range {text!r} in {field!r}")
    return start, end


def _int(text: str, field: str) -> int:
    try:
        return int(text, 10)
    except ValueError as exc:
        raise ValueError(f"invalid cron integer {text!r} in {field!r}") from exc
