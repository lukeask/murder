"""Usage-status extraction helpers for interactive harness adapters."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from murder.llm.harnesses.models import (
    HarnessUsageStatus,
    HarnessUsageTotals,
    HarnessUsageWindow,
)
from murder.llm.harnesses.parsing import strip_ansi

_CLAUDE_USAGE_RE = re.compile(
    r"Usage:\s*(?P<input>\d+)\s+input,\s*"
    r"(?P<output>\d+)\s+output,\s*"
    r"(?P<cache_read>\d+)\s+cache read,\s*"
    r"(?P<cache_write>\d+)\s+cache write",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"(?P<pct>\d+(?:\.\d+)?)\s*%\s+used", re.IGNORECASE)
_RESET_RE = re.compile(
    r"\bResets?\s+"
    r"(?:(?P<mon>[A-Za-z]{3,9})\s+(?P<mday>\d{1,2}),?\s*)?"
    r"(?P<time>\d{1,2}(?::\d{2})?\s*(?:am|pm))"
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


def _parse_reset_from_match(match: re.Match, now: datetime | None) -> str | None:
    tz_name = match.group("tz") or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    base = now.astimezone(tz) if now else datetime.now(tz=tz)
    raw_time = match.group("time").replace(" ", "")
    # Claude's /usage renders bare-hour resets like `12am`; minutes optional.
    fmt = "%I:%M%p" if ":" in raw_time else "%I%p"
    parsed = datetime.strptime(raw_time, fmt)

    mon_str = match.group("mon")
    mday_str = match.group("mday")
    if mon_str and mday_str and (mon_num := _MONTHS.get(mon_str[:3].lower())):
        day_n, year = int(mday_str), base.year
        if (mon_num, day_n) < (base.month, base.day):
            year += 1
        try:
            return base.replace(
                year=year, month=mon_num, day=day_n,
                hour=parsed.hour, minute=parsed.minute,
                second=0, microsecond=0,
            ).isoformat()
        except ValueError:
            return None

    reset_at = base.replace(hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0)
    if reset_at <= base:
        reset_at += timedelta(days=1)
    return reset_at.isoformat()


def _parse_reset_at(text: str, now: datetime | None = None) -> str | None:
    # Take the last match so stale /usage scrollback above the current overlay
    # doesn't win over the fresh reset time at the bottom of the pane.
    matches = list(_RESET_RE.finditer(text))
    if not matches:
        return None
    return _parse_reset_from_match(matches[-1], now)


def _reset_after_label(label: str, text: str, now: datetime | None) -> str | None:
    """First reset time appearing after the latest occurrence of label (scrollback-safe)."""
    idx = text.lower().rfind(label.lower())
    if idx < 0:
        return None
    matches = list(_RESET_RE.finditer(text[idx:]))
    if not matches:
        return None
    return _parse_reset_from_match(matches[0], now)


def _first_percent_after(label: str, text: str) -> float | None:
    # rfind: when scrollback contains old overlays, take the latest occurrence.
    idx = text.lower().rfind(label.lower())
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

    windows: list[HarnessUsageWindow] = []
    session_pct = _first_percent_after("Current session", clean)
    session_reset = _reset_after_label("Current session", clean, now)
    if session_pct is not None or session_reset:
        windows.append(HarnessUsageWindow(
            name="current_session",
            percent_used=session_pct,
            reset_at=session_reset,
        ))
    weekly_pct = _first_percent_after("Current week", clean)
    weekly_reset = _reset_after_label("Current week", clean, now)
    if weekly_pct is not None or weekly_reset:
        windows.append(HarnessUsageWindow(
            name="current_week",
            percent_used=weekly_pct,
            reset_at=weekly_reset,
        ))

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


def _local_clock_base(now: datetime | None) -> datetime:
    """Wall-clock anchor for bare reset times from harness panes (local TZ)."""
    if now is None:
        return datetime.now().astimezone()
    if now.tzinfo is None:
        return now.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return now.astimezone()


def _parse_clock_reset(raw: str, now: datetime | None) -> str | None:
    """Parse a `21:29` / `6:30pm` / `14:49 on 18 May` reset hint into ISO."""
    match = _CLOCK_RESET_RE.search(raw)
    if not match:
        return None
    base = _local_clock_base(now)
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
    # Usage probe sessions keep /status scrollback; take the latest row per window.
    windows_by_name: dict[str, HarnessUsageWindow] = {}
    window_order: list[str] = []

    for match in _CODEX_LIMIT_RE.finditer(clean):
        pct = float(match.group("pct"))
        direction = (match.group("dir") or "used").lower()
        used = 100.0 - pct if direction in ("left", "remaining") else pct
        used = round(max(0.0, min(100.0, used)), 4)
        name = re.sub(r"\s+", " ", match.group("label")).strip().lower() or "usage"
        reset_raw = match.group("reset") or ""
        # Newer Codex builds put `(resets 21:29)` inline; older ones put a
        # standalone `Resets 9:15am (TZ)` line — fall back to the latter.
        reset_at = (
            _parse_clock_reset(reset_raw, now) if reset_raw else _parse_reset_at(clean, now=now)
        )
        if name not in windows_by_name:
            window_order.append(name)
        windows_by_name[name] = HarnessUsageWindow(name=name, percent_used=used, reset_at=reset_at)

    windows = [windows_by_name[name] for name in window_order]

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


# Antigravity's `/usage` opens a "Model Quota" dialog (verified against agy
# 1.0.7, fixture `agy_usage_dialog.txt`). Each model renders as three lines:
#   `  Claude Opus 4.6 (Thinking)`
#   `  ███████████ ░░░ ... 20%`          <- bar tracks quota REMAINING
#   `  20% remaining · Refreshes in 12h 39m`   (or `Quota available`)
# `percent_used` is normalized to consumed quota (100 - remaining), matching
# the HarnessUsageWindow contract. `reset_at` is now + the "Refreshes in"
# delta. Effort variants of the same base model with identical (percent,
# reset) collapse into one window named by the base model so the usage panel
# doesn't gain a row per effort level.
_AGY_MODEL_LINE_RE = re.compile(
    r"^\s{0,8}(?P<label>[A-Za-z][\w.\- ]{1,50}\((?:Low|Medium|High|Thinking)\))\s*$",
)
_AGY_EFFORT_SUFFIX_RE = re.compile(r"\s*\((?:Low|Medium|High|Thinking)\)\s*$")
_AGY_BAR_PCT_RE = re.compile(r"(?P<pct>\d+(?:\.\d+)?)\s*%\s*$")
_AGY_REMAINING_RE = re.compile(r"(?P<pct>\d+(?:\.\d+)?)\s*%\s*remaining", re.IGNORECASE)
_AGY_AVAILABLE_RE = re.compile(r"Quota available", re.IGNORECASE)
_AGY_REFRESH_RE = re.compile(
    r"Refreshes in\s+(?:(?P<d>\d+)\s*d)?\s*(?:(?P<h>\d+)\s*h)?\s*(?:(?P<m>\d+)\s*m)?",
    re.IGNORECASE,
)
_AGY_PLAN_RE = re.compile(r"\((?P<plan>[^()\n]*Quota[^()\n]*)\)")
_AGY_FOOTER_RE = re.compile(r"esc to cancel|↑/↓ Scroll", re.IGNORECASE)


def _agy_refresh_to_reset(line: str, now: datetime | None) -> str | None:
    match = _AGY_REFRESH_RE.search(line)
    if not match or not any(match.group(g) for g in ("d", "h", "m")):
        return None
    base = _local_clock_base(now)
    delta = timedelta(
        days=int(match.group("d") or 0),
        hours=int(match.group("h") or 0),
        minutes=int(match.group("m") or 0),
    )
    return (base + delta).replace(microsecond=0).isoformat()


def parse_antigravity_usage_pane(
    pane_text: str,
    *,
    fetched_at: str | None = None,
    now: datetime | None = None,
) -> HarnessUsageStatus:
    clean = strip_ansi(pane_text)
    plan_match = _AGY_PLAN_RE.search(clean)

    # Scrollback safety: only parse below the LAST "Model Quota" dialog header.
    anchor = clean.lower().rfind("model quota")
    body = clean[anchor:] if anchor >= 0 else ""
    lines = body.splitlines()

    # raw rows: (full label, percent_used, reset_at) in display order
    raw_rows: list[tuple[str, float, str | None]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _AGY_FOOTER_RE.search(line):
            break
        label_match = _AGY_MODEL_LINE_RE.match(line)
        if not label_match:
            i += 1
            continue
        label = re.sub(r"\s+", " ", label_match.group("label")).strip()
        remaining: float | None = None
        reset_at: str | None = None
        # Look at the next few non-empty lines (bar + status) for this row.
        j = i + 1
        consumed = i
        while j < min(i + 4, len(lines)):
            row = lines[j]
            if not row.strip():
                j += 1
                continue
            if _AGY_MODEL_LINE_RE.match(row) or _AGY_FOOTER_RE.search(row):
                break
            if status_match := _AGY_REMAINING_RE.search(row):
                remaining = float(status_match.group("pct"))
                reset_at = _agy_refresh_to_reset(row, now)
                consumed = j
            elif _AGY_AVAILABLE_RE.search(row):
                remaining = remaining if remaining is not None else 100.0
                consumed = j
            elif bar_match := _AGY_BAR_PCT_RE.search(row):
                if remaining is None:
                    remaining = float(bar_match.group("pct"))
                consumed = j
            j += 1
        if remaining is not None:
            used = round(max(0.0, min(100.0, 100.0 - remaining)), 4)
            raw_rows.append((label, used, reset_at))
        i = max(consumed, i) + 1

    # Collapse effort variants: same base model AND identical (percent, reset)
    # merge into one window named by the base model. Divergent variants keep
    # their full labels so the difference stays visible.
    by_base: dict[str, list[tuple[str, float, str | None]]] = {}
    base_order: list[str] = []
    for label, used, reset_at in raw_rows:
        base = _AGY_EFFORT_SUFFIX_RE.sub("", label).strip()
        if base not in by_base:
            by_base[base] = []
            base_order.append(base)
        by_base[base].append((label, used, reset_at))

    windows: list[HarnessUsageWindow] = []
    for base in base_order:
        rows = by_base[base]
        stats = {(used, reset_at) for _, used, reset_at in rows}
        if len(stats) == 1:
            used, reset_at = next(iter(stats))
            windows.append(HarnessUsageWindow(name=base, percent_used=used, reset_at=reset_at))
        else:
            for label, used, reset_at in rows:
                windows.append(
                    HarnessUsageWindow(name=label, percent_used=used, reset_at=reset_at)
                )

    return HarnessUsageStatus(
        harness="antigravity",
        source="slash:/usage",
        fetched_at=fetched_at or utc_now_iso(),
        plan=plan_match.group("plan").strip() if plan_match else None,
        windows=windows,
    )
