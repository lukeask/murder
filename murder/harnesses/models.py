from __future__ import annotations

from dataclasses import dataclass
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
