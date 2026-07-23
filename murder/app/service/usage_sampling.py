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


async def sample_usage(
    *, repo_root: Path, db: sqlite3.Connection, modes: set[str] | None = None
) -> dict[str, Any]:
    """Persist a usage sample directly in its feature-owned repository."""
    context = UsageSamplingContext(config=Config.load(repo_root), repo_root=repo_root, db=db)
    sampled_kinds = harness_kinds_to_sample(context, modes=modes)
    stored, failures = await sample_harness_usages(context, modes=modes)
    return {
        "handled": True,
        "stored": stored,
        "failures": failures,
        "sampled_kinds": sampled_kinds,
    }


__all__ = ["sample_usage"]
