"""Usage-status extraction helpers for interactive harness adapters."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from murder.llm.harnesses.models import (
    HarnessUsageContextWindow,
    HarnessUsageFreshness,
    HarnessUsageNotice,
    HarnessUsageStatus,
    HarnessUsageTotals,
    HarnessUsageWindow,
)
from murder.llm.harnesses.parsing import strip_ansi

LOGGER = logging.getLogger(__name__)

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
        LOGGER.debug("unknown usage reset timezone %r; defaulting to UTC", tz_name)
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
            LOGGER.debug(
                "usage reset date out of range (mon=%r mday=%r time=%r); dropping reset_at",
                mon_str,
                mday_str,
                match.group("time"),
                exc_info=True,
            )
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


def _reset_from(text: str, idx: int, now: datetime | None) -> str | None:
    """First reset time appearing at/after idx (the resolved bar's anchor)."""
    if idx < 0:
        return None
    matches = list(_RESET_RE.finditer(text[idx:]))
    if not matches:
        return None
    return _parse_reset_from_match(matches[0], now)


def _percent_from(text: str, idx: int) -> float | None:
    if idx < 0:
        return None
    match = _PERCENT_RE.search(text[idx:])
    return float(match.group("pct")) if match else None


def _session_anchor(text: str) -> int:
    # rfind: when scrollback contains old overlays, take the latest occurrence.
    return text.lower().rfind("current session")


def _week_anchor(text: str) -> int:
    """Resolve the aggregate weekly bar, ignoring per-model "(... only)" sub-bars.

    Max plans render up to three bars: session, "Current week (all models)", and
    "Current week (Sonnet only)". Both weekly bars start with "Current week", so a
    naive rfind lands on the Sonnet-only sub-bar. Prefer the explicit aggregate
    label, then fall back to the latest "Current week" line that isn't model-scoped.
    """
    lower = text.lower()
    idx = lower.rfind("current week (all models)")
    if idx >= 0:
        return idx
    search_end = len(text)
    while True:
        idx = lower.rfind("current week", 0, search_end)
        if idx < 0:
            return -1
        line_end = lower.find("\n", idx)
        line = lower[idx : line_end if line_end >= 0 else len(lower)]
        if "only)" not in line:
            return idx
        search_end = idx


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
    session_idx = _session_anchor(clean)
    session_pct = _percent_from(clean, session_idx)
    session_reset = _reset_from(clean, session_idx, now)
    if session_pct is not None or session_reset:
        windows.append(HarnessUsageWindow(
            name="current_session",
            percent_used=session_pct,
            reset_at=session_reset,
        ))
    week_idx = _week_anchor(clean)
    weekly_pct = _percent_from(clean, week_idx)
    weekly_reset = _reset_from(clean, week_idx, now)
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
    r"^(?P<label>[^:]{1,80}?\blimits?)\s*:\s*"
    r"(?:\[[^\]]*\]\s*)?(?P<pct>\d+(?:\.\d+)?)\s*%"
    r"(?:\s*(?P<dir>left|remaining|used|consumed))?"
    r"(?P<tail>.*)$",
    re.IGNORECASE,
)
_CODEX_SESSION_RE = re.compile(
    r"\bSession:\s*(?P<session>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
    re.IGNORECASE,
)
_CLOCK_RESET_RE = re.compile(
    r"\b(?P<h>\d{1,2}):(?P<m>\d{2})(?:\s*(?P<ampm>am|pm))?"
    r"(?:\s+on\s+(?:(?P<day>\d{1,2})\s+(?P<mon>[A-Za-z]{3,})|(?P<mon2>[A-Za-z]{3,})\s+(?P<day2>\d{1,2})))?",
    re.IGNORECASE,
)
_CODEX_HEADING_RE = re.compile(r"(?:>_\s*)?OpenAI\s+Codex\s*\(v[^)]*\)", re.I)
_CODEX_FIELD_RE = re.compile(r"^(?P<label>[A-Za-z][A-Za-z ._-]{1,40}):\s*(?P<value>.+)$")
_CODEX_CONTEXT_RE = re.compile(
    r"^Context\s+window\s*:\s*(?P<pct>\d+(?:\.\d+)?)\s*%\s*(?P<dir>left|remaining|used|consumed)"
    r"(?:\s*\(\s*(?P<used>\d+(?:\.\d+)?)\s*(?P<unit>[KMGT]?)\s*used\s*/\s*"
    r"(?P<limit>\d+(?:\.\d+)?)\s*(?P<limit_unit>[KMGT]?)\s*\))?",
    re.I,
)
_STALE_NOTICE_RE = re.compile(r"\blimits?\s+may\s+be\s+(?:stale|out\s+of\s+date)\b", re.I)
_HARD_LIMIT_RE = re.compile(r"you(?:'|’)ve\s+hit\s+your\s+usage\s+limit", re.I)
_RESET_CREDIT_RE = re.compile(
    r"you\s+have\s+(?P<count>\d+)\s+usage\s+limit\s+resets?\s+available", re.I
)


@dataclass(frozen=True, slots=True)
class CodexStatusSurface:
    """The bounded status panel selected from a terminal capture."""

    start: int
    end: int
    complete: bool
    active: bool
    lines: tuple[str, ...]
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
    # ``now`` is supplied by callers/tests specifically to establish the
    # harness-local wall clock. Converting it through the host's local zone
    # changes the calendar clock (e.g. Eastern → Mountain) and turns a
    # 45-minute reset into a multi-hour one. Preserve an explicit offset/zone.
    return now


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
            LOGGER.debug(
                "codex/agy reset clock out of range (raw=%r day=%r mon=%r); dropping reset_at",
                raw,
                day,
                mon,
                exc_info=True,
            )
            return None
    reset_at = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if reset_at <= base:
        reset_at += timedelta(days=1)
    return reset_at.isoformat()


def locate_codex_status_surface(pane_text: str) -> CodexStatusSurface | None:
    """Locate the newest structurally valid Codex status panel.

    Selection is intentionally independent of quota labels: an update may
    remove a window, and scrollback must never lend it back from an old panel.
    """
    lines = strip_ansi(pane_text).splitlines()
    last_composer = max(
        (i for i, line in enumerate(lines) if line.lstrip().startswith("›")), default=-1
    )
    candidates: list[CodexStatusSurface] = []
    for start, line in enumerate(lines):
        if not _CODEX_HEADING_RE.search(line):
            continue
        next_heading = next(
            (i for i in range(start + 1, len(lines)) if _CODEX_HEADING_RE.search(lines[i])),
            len(lines),
        )
        close = next(
            (i for i in range(start + 1, next_heading) if lines[i].lstrip().startswith(("╰", "+"))),
            None,
        )
        end = (close + 1) if close is not None else next_heading
        content = [_clean_codex_line(item) for item in lines[start + 1:end]]
        labels = {
            match.group("label").strip().lower()
            for item in content
            if (match := _CODEX_FIELD_RE.match(item)) is not None
        }
        if not labels.intersection({"session", "account", "collaboration mode"}):
            continue
        # Borderless captures are accepted only when another status heading or
        # composer bounds them; an unbounded rendering remains incomplete.
        complete = close is not None or end < len(lines)
        candidates.append(
            CodexStatusSurface(start, end, complete, start > last_composer, tuple(lines[start:end]))
        )
    completed = [candidate for candidate in candidates if candidate.complete]
    return (completed or candidates or [None])[-1]


def _clean_codex_line(line: str) -> str:
    return line.strip().strip("│").strip()


def _codex_key(label: str) -> str:
    normalized = re.sub(r"\s+", " ", label).strip().lower()
    aliases = {"5h limit": "5h", "5 hour limit": "5h", "weekly limit": "weekly"}
    return aliases.get(normalized, re.sub(r"\s+limit$", "", normalized))


def _codex_notices(lines: list[str]) -> list[HarnessUsageNotice]:
    notices: list[HarnessUsageNotice] = []
    for line in lines:
        text = _clean_codex_line(line)
        if _STALE_NOTICE_RE.search(text):
            notices.append(HarnessUsageNotice("stale_limits", text))
        elif _HARD_LIMIT_RE.search(text):
            notices.append(HarnessUsageNotice("hard_limit", text))
        elif match := _RESET_CREDIT_RE.search(text):
            notices.append(
                HarnessUsageNotice("reset_credit_available", text, int(match.group("count")))
            )
    return notices


def parse_codex_status_pane(
    pane_text: str,
    *,
    fetched_at: str | None = None,
    now: datetime | None = None,
) -> HarnessUsageStatus:
    clean = strip_ansi(pane_text)
    surface = locate_codex_status_surface(clean)
    panel_lines = list(surface.lines) if surface else []
    diagnostics: list[str] = []
    windows: list[HarnessUsageWindow] = []
    context: HarnessUsageContextWindow | None = None
    session_id: str | None = None
    for index, raw_line in enumerate(panel_lines):
        line = _clean_codex_line(raw_line)
        if session_match := _CODEX_SESSION_RE.search(line):
            session_id = session_match.group("session")
        if context_match := _CODEX_CONTEXT_RE.match(line):
            pct = float(context_match.group("pct"))
            direction = context_match.group("dir").lower()
            used = pct if direction in {"used", "consumed"} else 100.0 - pct
            context = HarnessUsageContextWindow(
                percent_used=round(max(0.0, min(100.0, used)), 4),
                percent_left=pct if direction in {"left", "remaining"} else 100.0 - pct,
                used=float(context_match.group("used")) if context_match.group("used") else None,
                limit=float(context_match.group("limit")) if context_match.group("limit") else None,
                unit=context_match.group("unit") or context_match.group("limit_unit") or None,
            )
            continue
        match = _CODEX_LIMIT_RE.match(line)
        if match is None:
            continue
        direction = (match.group("dir") or "").lower()
        label = re.sub(r"\s+", " ", match.group("label")).strip()
        if not direction:
            diagnostics.append(f"ambiguous quota direction for {label!r}")
            continue
        pct = float(match.group("pct"))
        used = pct if direction in {"used", "consumed"} else 100.0 - pct
        reset_text = match.group("tail")
        # A wrapped reset can only belong to the immediately preceding row.
        if "reset" not in reset_text.lower() and index + 1 < len(panel_lines):
            following = _clean_codex_line(panel_lines[index + 1])
            if following.lower().startswith("resets"):
                reset_text = following
        reset_at = _parse_clock_reset(reset_text, now) if "reset" in reset_text.lower() else None
        if "reset" in reset_text.lower() and reset_at is None:
            diagnostics.append(f"invalid reset for {label!r}: {reset_text!r}")
        key = _codex_key(label)
        windows.append(HarnessUsageWindow(
            # Preserve the long-standing public labels for known Codex rows;
            # future labels remain visible instead of being collapsed away.
            name=key if key in {"5h", "weekly"} else label,
            key=key,
            percent_used=round(max(0.0, min(100.0, used)), 4),
            reset_at=reset_at,
        ))

    # Notices outside /status are meaningful only in the active view below
    # the last composer; historical scrollback must not become current state.
    active_lines = clean.splitlines()
    last_composer = max(
        (i for i, line in enumerate(active_lines) if line.lstrip().startswith("›")), default=-1
    )
    notices = _codex_notices(panel_lines)
    if last_composer >= 0:
        notices.extend(_codex_notices(active_lines[last_composer:]))
    freshness = (
        HarnessUsageFreshness.ADVISORY_STALE if any(n.kind == "stale_limits" for n in notices)
        else HarnessUsageFreshness.CURRENT if surface and surface.complete
        else HarnessUsageFreshness.UNKNOWN
    )

    return HarnessUsageStatus(
        harness="codex",
        source="slash:/status",
        fetched_at=fetched_at or utc_now_iso(),
        windows=windows,
        freshness=freshness,
        notices=notices,
        context_window=context,
        diagnostics=diagnostics,
        surface_bounds=(surface.start + 1, surface.end) if surface else None,
        surface_complete=bool(surface and surface.complete),
        parser_version="codex-status-v2",
        raw={"session_id": session_id} if session_id else {},
    )


# Antigravity's `/usage` has used two quota-dialog layouts:
#
# agy 1.0.7: a "Model Quota" dialog (fixture `agy_usage_dialog.txt`), where
# each model renders as three lines:
#   `  Claude Opus 4.6 (Thinking)`
#   `  ███████████ ░░░ ... 20%`          <- bar tracks quota REMAINING
#   `  20% remaining · Refreshes in 12h 39m`   (or `Quota available`)
#
# agy 1.0.10: a "Models & Quota" dialog (fixture
# `agy_usage_dialog_grouped.txt`), where model families share a weekly limit:
#   `GEMINI MODELS`
#   `  Models within this group: Gemini Flash, Gemini Pro`
#   `  Weekly Limit`
#   `    [████░░░] 85.61%`
#   `    86% remaining · Refreshes in 157h 26m`
#
# `percent_used` is normalized to consumed quota (100 - remaining), matching
# the HarnessUsageWindow contract. The bracketed bars also track remaining
# quota; the textual remaining line is rounded, so the parser keeps the bar's
# more precise percent when both are present. `reset_at` is now + the
# "Refreshes in" delta. For the old layout, effort variants of the same base
# model with identical (percent, reset) collapse into one window named by the
# base model so the usage panel doesn't gain a row per effort level.
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
_AGY_GROUP_HEADER_RE = re.compile(r"^\s*(?P<label>[A-Z][A-Z /&-]+MODELS)\s*$")
_AGY_WEEKLY_LIMIT_RE = re.compile(r"^\s*Weekly Limit\s*$", re.IGNORECASE)


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


def _agy_window_used(remaining: float) -> float:
    return round(max(0.0, min(100.0, 100.0 - remaining)), 4)


def _agy_group_label(label: str) -> str:
    words = re.sub(r"\s+", " ", label).strip().split(" ")
    out: list[str] = []
    for word in words:
        upper = word.upper()
        if upper in {"GPT", "GPT-OSS"}:
            out.append(upper)
        elif upper == "AND":
            out.append("and")
        else:
            out.append(word.capitalize())
    return " ".join(out)


def _parse_antigravity_grouped_windows(
    lines: list[str],
    *,
    now: datetime | None,
) -> list[HarnessUsageWindow]:
    windows: list[HarnessUsageWindow] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if _AGY_FOOTER_RE.search(line):
            break
        group_match = _AGY_GROUP_HEADER_RE.match(line)
        if not group_match:
            i += 1
            continue

        label = _agy_group_label(group_match.group("label"))
        remaining: float | None = None
        reset_at: str | None = None
        j = i + 1
        while j < len(lines):
            row = lines[j]
            if j > i + 1 and _AGY_GROUP_HEADER_RE.match(row):
                break
            if _AGY_FOOTER_RE.search(row):
                break
            if _AGY_WEEKLY_LIMIT_RE.match(row):
                j += 1
                continue
            if bar_match := _AGY_BAR_PCT_RE.search(row):
                # The bracketed percentage is a more precise remaining value
                # than the rounded prose line that follows.
                if "[" in row and "]" in row:
                    remaining = float(bar_match.group("pct"))
            if status_match := _AGY_REMAINING_RE.search(row):
                if remaining is None:
                    remaining = float(status_match.group("pct"))
                reset_at = _agy_refresh_to_reset(row, now)
            elif _AGY_AVAILABLE_RE.search(row):
                remaining = remaining if remaining is not None else 100.0
            j += 1

        if remaining is not None:
            windows.append(
                HarnessUsageWindow(
                    name=label,
                    percent_used=_agy_window_used(remaining),
                    reset_at=reset_at,
                )
            )
        i = max(j, i + 1)
    return windows


def parse_antigravity_usage_pane(
    pane_text: str,
    *,
    fetched_at: str | None = None,
    now: datetime | None = None,
) -> HarnessUsageStatus:
    clean = strip_ansi(pane_text)
    plan_match = _AGY_PLAN_RE.search(clean)

    # Scrollback safety: only parse below the LAST quota dialog header.
    lower = clean.lower()
    anchor = max(lower.rfind("models & quota"), lower.rfind("model quota"))
    body = clean[anchor:] if anchor >= 0 else ""
    lines = body.splitlines()

    grouped_windows = _parse_antigravity_grouped_windows(lines, now=now)
    if grouped_windows:
        return HarnessUsageStatus(
            harness="antigravity",
            source="slash:/usage",
            fetched_at=fetched_at or utc_now_iso(),
            plan=plan_match.group("plan").strip() if plan_match else None,
            windows=grouped_windows,
        )

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
                if remaining is None:
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
            raw_rows.append((label, _agy_window_used(remaining), reset_at))
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
