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


def parse_codex_status_pane(
    pane_text: str,
    *,
    fetched_at: str | None = None,
    now: datetime | None = None,
) -> HarnessUsageStatus:
    clean = strip_ansi(pane_text)
    windows: list[HarnessUsageWindow] = []

    for label in ("session", "weekly", "daily", "5h"):
        pct = _first_percent_after(label, clean)
        if pct is not None:
            windows.append(
                HarnessUsageWindow(
                    name=label,
                    percent_used=pct,
                    reset_at=_parse_reset_at(clean, now=now),
                )
            )

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
