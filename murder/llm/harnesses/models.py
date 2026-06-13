from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class HarnessStartSpec:
    cwd: Path
    startup_model: str | None = None
    startup_effort: str | None = None
    additional_workspace_dirs: tuple[Path, ...] = ()
    ready_timeout_s: float = 240.0
    poll_interval_s: float = 0.4
    auto_run: bool | None = None
    # CC-only: when set, the harness launch resumes a prior session id
    # (``claude --resume <id>``) instead of starting a fresh conversation.
    resume_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class HarnessModelChoice:
    index: int | None
    model_id: str
    label: str
    current: bool = False


@dataclass(frozen=True, slots=True)
class HarnessEffortChoice:
    index: int | None
    effort: str
    label: str
    current: bool = False


@dataclass(frozen=True, slots=True)
class HarnessModelState:
    model: str | None = None
    effort: str | None = None


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
    """One usage/quota window for a harness (a billing or rate-limit period).

    `percent_used` is the contract every adapter must honor: quota *consumed*,
    as a percentage 0–100 — never quota remaining. Each adapter normalizes to
    this when it parses; the display layer is provider-agnostic and trusts it.

    `used`/`limit` are optional raw counts shown for context only. Do NOT
    re-derive `percent_used` from them — a provider may report a percentage
    that does not equal `used/limit` (e.g. Cursor's request counts are not a
    clean used-of-limit pair).
    """

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
