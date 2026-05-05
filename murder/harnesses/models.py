from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class HarnessStartSpec:
    cwd: Path
    startup_model: str | None = None
    ready_timeout_s: float = 240.0
    poll_interval_s: float = 0.4
    auto_run: bool | None = None


@dataclass(slots=True)
class HarnessPaneState:
    ready: bool
    idle: bool
    busy: bool


@dataclass(slots=True)
class HarnessUsageTotals:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None
    api_duration_s: float | None = None
    wall_duration_s: float | None = None
    lines_added: int | None = None
    lines_removed: int | None = None


@dataclass(slots=True)
class HarnessUsageWindow:
    name: str
    percent_used: float | None = None
    reset_at: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    used: int | float | None = None
    limit: int | float | None = None
    unit: str | None = None


@dataclass(slots=True)
class HarnessUsageStatus:
    harness: str
    source: str
    fetched_at: str
    plan: str | None = None
    windows: list[HarnessUsageWindow] = field(default_factory=list)
    session: HarnessUsageTotals | None = None
    messages: list[str] = field(default_factory=list)
    raw: dict[str, object] = field(default_factory=dict)
