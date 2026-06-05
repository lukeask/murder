"""Shared accessor for the `harness_usage_snapshots.status_json` payload.

The snapshot payload is the json-serialized :class:`HarnessUsageStatus` (see
``insert_harness_usage_snapshot``). Both the scheduler worker and the service-side
schedule snapshot read it; routing every reader through this one parser keeps the
payload shape in a single place rather than duplicated across consumers (which
would drift the day the shape changes).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class UsageWindow:
    """One quota/rate-limit window from a usage snapshot payload."""

    name: str
    percent_used: float | None
    reset_at: str | None
    starts_at: str | None
    ends_at: str | None

    @property
    def window_key(self) -> str:
        """The stable key callers group/look-up by (`name`, or `usage` if blank)."""
        return self.name or "usage"


@dataclass(frozen=True)
class UsageStatusSnapshot:
    """Parsed view over a single `status_json` payload."""

    windows: tuple[UsageWindow, ...]

    @classmethod
    def from_json(cls, status_json: Any) -> UsageStatusSnapshot | None:
        """Parse a `status_json` string, or return None when it isn't usable."""
        try:
            payload = json.loads(status_json)
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, Mapping):
            return None
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> UsageStatusSnapshot:
        windows: list[UsageWindow] = []
        for raw in payload.get("windows") or []:
            if not isinstance(raw, Mapping):
                continue
            pct = raw.get("percent_used")
            windows.append(
                UsageWindow(
                    name=str(raw.get("name") or ""),
                    percent_used=float(pct) if isinstance(pct, (int, float)) else None,
                    reset_at=_opt_str(raw.get("reset_at")),
                    starts_at=_opt_str(raw.get("starts_at")),
                    ends_at=_opt_str(raw.get("ends_at")),
                )
            )
        return cls(tuple(windows))

    def first_percent_used(self) -> float | None:
        """percent_used of the first window that reports one, else None."""
        for window in self.windows:
            if window.percent_used is not None:
                return window.percent_used
        return None

    def percent_for(self, window_key: str) -> float | None:
        """percent_used for the window with this key, else None."""
        for window in self.windows:
            if window.window_key == window_key:
                return window.percent_used
        return None


__all__ = ["UsageStatusSnapshot", "UsageWindow"]
