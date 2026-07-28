"""Direct application service for durable harness-usage sampling."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from murder.config import Config
from murder.llm.harnesses.usage_sampling import (
    UsageSamplingContext,
    harness_kinds_to_sample,
    sample_harness_usages,
)
from murder.runtime.scheduler.projection import invalidate_schedule


async def sample_usage(
    *, repo_root: Path, db: sqlite3.Connection, modes: set[str] | None = None
) -> dict[str, Any]:
    """Persist a usage sample and invalidate the schedule projection for the TUI.

    Successful inserts become durable ``projection_inputs`` rows for ``schedule``.
    The socket server tails that log and broadcasts ``projection.invalidate``,
    which makes connected Ink clients call ``usage.refresh()``.
    """
    context = UsageSamplingContext(config=Config.load(repo_root), repo_root=repo_root, db=db)
    sampled_kinds = harness_kinds_to_sample(context, modes=modes)
    stored, failures = await sample_harness_usages(context, modes=modes)
    if stored > 0:
        for kind in sampled_kinds:
            invalidate_schedule(db, subject_key=f"usage:{kind}")
    return {
        "handled": True,
        "stored": stored,
        "failures": failures,
        "sampled_kinds": sampled_kinds,
    }


__all__ = ["sample_usage"]
