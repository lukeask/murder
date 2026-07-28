"""Codex account rate-limits side channel.

Codex TUI ``/status`` subscription usage is mirrored by the app-server RPC
``account/rateLimits/read``. Background sampling uses that RPC so Murder never
opens a terminal slash overlay just to refresh quotas.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from murder.llm.harness_control.app_server.client import AppServerClient
from murder.llm.harness_control.app_server.connection import AppServerConnection
from murder.llm.harnesses.models import HarnessUsageStatus, HarnessUsageWindow
from murder.llm.harnesses.usage import utc_now_iso

LOGGER = logging.getLogger(__name__)

_FIVE_HOURS_MINS = 5 * 60
_ONE_WEEK_MINS = 7 * 24 * 60
_DURATION_TOLERANCE_MINS = 30


class CodexUsageError(Exception):
    """Base exception for Codex rate-limits collection failures."""


def _unix_to_iso(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    raw = float(value)
    # Heuristic: millisecond epochs are ≥ ~1e12 for dates after 2001.
    seconds = raw / 1000.0 if raw >= 1_000_000_000_000 else raw
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        LOGGER.debug("invalid Codex rate-limit resetsAt=%r", value, exc_info=True)
        return None


def _window_name(slot: str, duration_mins: object) -> str:
    """Map ChatGPT primary/secondary slots onto historical Codex labels when possible."""
    if isinstance(duration_mins, (int, float)) and not isinstance(duration_mins, bool):
        mins = float(duration_mins)
        if abs(mins - _FIVE_HOURS_MINS) <= _DURATION_TOLERANCE_MINS:
            return "5h"
        if abs(mins - _ONE_WEEK_MINS) <= _DURATION_TOLERANCE_MINS * 24:
            return "weekly"
        if mins >= _ONE_WEEK_MINS - _DURATION_TOLERANCE_MINS * 24:
            days = round(mins / (24 * 60))
            if days > 0:
                return f"{days}d"
        hours = round(mins / 60)
        if hours > 0:
            return f"{hours}h"
    return slot


def _window_from_row(slot: str, row: Mapping[str, Any]) -> HarnessUsageWindow | None:
    percent = row.get("usedPercent")
    if not isinstance(percent, (int, float)) or isinstance(percent, bool):
        return None
    duration = row.get("windowDurationMins")
    name = _window_name(slot, duration)
    reset_at = _unix_to_iso(row.get("resetsAt"))
    starts_at = None
    if (
        reset_at is not None
        and isinstance(duration, (int, float))
        and not isinstance(duration, bool)
        and duration > 0
    ):
        try:
            ends = datetime.fromisoformat(reset_at)
            starts_at = (ends - timedelta(minutes=float(duration))).isoformat()
        except ValueError:
            starts_at = None
    return HarnessUsageWindow(
        name=name,
        key=slot,
        percent_used=float(percent),
        reset_at=reset_at,
        starts_at=starts_at,
        ends_at=reset_at,
        unit="percent",
    )


def rate_limits_to_usage_status(
    payload: Mapping[str, Any],
    *,
    fetched_at: str | None = None,
) -> HarnessUsageStatus:
    """Compose ``HarnessUsageStatus`` from an ``account/rateLimits/read`` result."""
    rate_limits = payload.get("rateLimits")
    if not isinstance(rate_limits, Mapping):
        # Some transports stash the inner object directly.
        rate_limits = payload if "primary" in payload or "secondary" in payload else {}

    windows: list[HarnessUsageWindow] = []
    if isinstance(rate_limits, Mapping):
        for slot in ("primary", "secondary"):
            row = rate_limits.get(slot)
            if isinstance(row, Mapping):
                window = _window_from_row(slot, row)
                if window is not None:
                    windows.append(window)

    plan = None
    if isinstance(rate_limits, Mapping):
        plan_raw = rate_limits.get("planType")
        if isinstance(plan_raw, str) and plan_raw.strip():
            plan = plan_raw.strip()

    return HarnessUsageStatus(
        harness="codex",
        source="app-server:account/rateLimits/read",
        fetched_at=fetched_at or utc_now_iso(),
        plan=plan,
        windows=windows,
        raw={
            "rateLimits": dict(rate_limits) if isinstance(rate_limits, Mapping) else {},
            "rateLimitsByLimitId": payload.get("rateLimitsByLimitId"),
        },
    )


async def get_usage_status(
    *,
    cwd: str | None = None,
    argv: tuple[str, ...] | None = None,
) -> HarnessUsageStatus:
    """Start a short-lived ``codex app-server``, read rate limits, and close."""
    connection = AppServerConnection(argv=argv, cwd=cwd)
    try:
        await connection.start()
        client = AppServerClient(connection)
        await client.initialize()
        result = await connection.request("account/rateLimits/read", {})
        if not isinstance(result, dict):
            raise CodexUsageError(
                f"account/rateLimits/read returned {type(result).__name__}, expected object"
            )
        return rate_limits_to_usage_status(result)
    except CodexUsageError:
        raise
    except Exception as exc:
        raise CodexUsageError(f"Codex rate-limits collection failed: {exc}") from exc
    finally:
        await connection.aclose()


__all__ = [
    "CodexUsageError",
    "get_usage_status",
    "rate_limits_to_usage_status",
]
