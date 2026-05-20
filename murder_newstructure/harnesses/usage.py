"""Usage-status extraction helpers for interactive harness adapters."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from murder.harnesses.models import (
    HarnessUsageStatus,
    HarnessUsageTotals,
    HarnessUsageWindow,
)
from murder.harnesses.parsing import strip_ansi

_CLAUDE_USAGE_RE = re.compile(
    r"Usage:\s*(?P<input>\d+)\s+input,\s*"
    r"(?P<output>\d+)\s+output,\s*"
    r"(?P<cache_read>\d+)\s+cache read,\s*"
    r"(?P<cache_write>\d+)\s+cache write",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"(?P<pct>\d+(?:\.\d+)?)\s*%\s+used", re.IGNORECASE)
_RESET_RE = re.compile(
    r"\bResets?\s+(?P<time>\d{1,2}:\d{2}\s*(?:am|pm))"
    r"(?:\s*\((?P<tz>[^)]+)\))?",
    re.IGNORECASE,
)
_COST_RE = re.compile(r"Total cost:\s*\$(?P<cost>\d+(?:\.\d+)?)", re.IGNORECASE)
_DURATION_RE = re.compile(
    r"Total duration \((?P<kind>API|wall)\):\s*(?P<value>\d+(?:\.\d+)?)s",
    re.IGNORECASE,
)
_CHANGES_RE = re.compile(
    r"Total code changes:\s*(?P<added>\d+)\s+lines added,\s*"
    r"(?P<removed>\d+)\s+lines removed",
    re.IGNORECASE,
)


def utc_now_iso() -> str:
    return datetime.now(tz=ZoneInfo("UTC")).isoformat()


def _parse_reset_at(text: str, now: datetime | None = None) -> str | None:
    match = _RESET_RE.search(text)
    if not match:
        return None

    tz_name = match.group("tz") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    base = now.astimezone(tz) if now else datetime.now(tz=tz)
    parsed = datetime.strptime(match.group("time").replace(" ", ""), "%I:%M%p")
    reset_at = base.replace(
        hour=parsed.hour,
        minute=parsed.minute,
        second=0,
        microsecond=0,
    )
    if reset_at <= base:
        reset_at += timedelta(days=1)
    return reset_at.isoformat()


def _first_percent_after(label: str, text: str) -> float | None:
    idx = text.lower().find(label.lower())
    if idx < 0:
        return None
    haystack = text[idx:]
    match = _PERCENT_RE.search(haystack)
    return float(match.group("pct")) if match else None


def parse_claude_usage_pane(
    pane_text: str,
    *,
    fetched_at: str | None = None,
    now: datetime | None = None,
) -> HarnessUsageStatus:
    clean = strip_ansi(pane_text)
    totals = HarnessUsageTotals()

    if match := _COST_RE.search(clean):
        totals.cost_usd = float(match.group("cost"))
    for match in _DURATION_RE.finditer(clean):
        if match.group("kind").lower() == "api":
            totals.api_duration_s = float(match.group("value"))
        else:
            totals.wall_duration_s = float(match.group("value"))
    if match := _CHANGES_RE.search(clean):
        totals.lines_added = int(match.group("added"))
        totals.lines_removed = int(match.group("removed"))
    if match := _CLAUDE_USAGE_RE.search(clean):
        totals.input_tokens = int(match.group("input"))
        totals.output_tokens = int(match.group("output"))
        totals.cache_read_tokens = int(match.group("cache_read"))
        totals.cache_write_tokens = int(match.group("cache_write"))

    window = HarnessUsageWindow(
        name="current_session",
        percent_used=_first_percent_after("Current session", clean),
        reset_at=_parse_reset_at(clean, now=now),
    )
    windows = [window] if window.percent_used is not None or window.reset_at else []

    return HarnessUsageStatus(
        harness="claude_code",
        source="slash:/usage",
        fetched_at=fetched_at or utc_now_iso(),
        windows=windows,
        session=totals,
    )


# Codex `/status` rows look like:
#   `  5h limit:      [███████████░] 97% left (resets 21:29)`
#   `  Weekly limit:  [███████████░] 94% left (resets 14:49 on 18 May)`
# The suffix says `left`/`remaining` (quota *remaining*) or `used` (quota
# *consumed*). The bar fill tracks the remaining fraction, so a near-full bar
# means low usage. `percent_used` is always normalized to consumed quota:
# `left`/`remaining` is converted with 100−x; `used` passes through.
_CODEX_LIMIT_RE = re.compile(
    r"(?P<label>[A-Za-z0-9][\w/.\- ]*?)\s+limits?\s*:?\s*"
    r"(?:\[[^\]]*\]\s*)?"
    r"(?P<pct>\d+(?:\.\d+)?)\s*%\s*(?P<dir>left|remaining|used)?"
    r"(?:[^()\n]*?\((?:resets?\s+)?(?P<reset>[^)\n]+?)\))?",
    re.IGNORECASE,
)
_CLOCK_RESET_RE = re.compile(
    r"\b(?P<h>\d{1,2}):(?P<m>\d{2})(?:\s*(?P<ampm>am|pm))?"
    r"(?:\s+on\s+(?:(?P<day>\d{1,2})\s+(?P<mon>[A-Za-z]{3,})|(?P<mon2>[A-Za-z]{3,})\s+(?P<day2>\d{1,2})))?",
    re.IGNORECASE,
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        ["", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
    if i
}


def _parse_clock_reset(raw: str, now: datetime | None) -> str | None:
    """Parse a `21:29` / `6:30pm` / `14:49 on 18 May` reset hint into ISO."""
    match = _CLOCK_RESET_RE.search(raw)
    if not match:
        return None
    base = now or datetime.now(tz=ZoneInfo("UTC"))
    hour, minute = int(match.group("h")), int(match.group("m"))
    ampm = (match.group("ampm") or "").lower()
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    day = match.group("day") or match.group("day2")
    mon = match.group("mon") or match.group("mon2")
    if day and mon and (mon_num := _MONTHS.get(mon[:3].lower())):
        day_n, year = int(day), base.year
        if (mon_num, day_n) < (base.month, base.day):
            year += 1
        try:
            return base.replace(
                year=year,
                month=mon_num,
                day=day_n,
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            ).isoformat()
        except ValueError:
            return None
    reset_at = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset_at <= base:
        reset_at += timedelta(days=1)
    return reset_at.isoformat()


def parse_codex_status_pane(
    pane_text: str,
    *,
    fetched_at: str | None = None,
    now: datetime | None = None,
) -> HarnessUsageStatus:
    clean = strip_ansi(pane_text)
    windows: list[HarnessUsageWindow] = []
    seen: set[str] = set()

    for match in _CODEX_LIMIT_RE.finditer(clean):
        pct = float(match.group("pct"))
        direction = (match.group("dir") or "used").lower()
        used = 100.0 - pct if direction in ("left", "remaining") else pct
        used = round(max(0.0, min(100.0, used)), 4)
        name = re.sub(r"\s+", " ", match.group("label")).strip().lower() or "usage"
        if name in seen:
            continue
        seen.add(name)
        reset_raw = match.group("reset") or ""
        # Newer Codex builds put `(resets 21:29)` inline; older ones put a
        # standalone `Resets 9:15am (TZ)` line — fall back to the latter.
        reset_at = (
            _parse_clock_reset(reset_raw, now) if reset_raw else _parse_reset_at(clean, now=now)
        )
        windows.append(HarnessUsageWindow(name=name, percent_used=used, reset_at=reset_at))

    if not windows and (match := _PERCENT_RE.search(clean)):
        windows.append(
            HarnessUsageWindow(
                name="usage",
                percent_used=float(match.group("pct")),
                reset_at=_parse_reset_at(clean, now=now),
            )
        )

    return HarnessUsageStatus(
        harness="codex",
        source="slash:/status",
        fetched_at=fetched_at or utc_now_iso(),
        windows=windows,
    )
