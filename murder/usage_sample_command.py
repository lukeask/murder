"""Shared harness usage sampling via the usage-probe worker.

Used by the TUI usage-panel `r` sample key, the headless service interval poll, and any
other caller that should issue the same ``state.harness_usage.sample`` command.
"""

from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

from murder.state.persistence.connection import RepoDb

log = logging.getLogger(__name__)

# Nominal 10m cadence; actual sleeps are drawn uniformly in [10m, 20m].
USAGE_SAMPLE_POLL_INTERVAL_S = 600.0
USAGE_SAMPLE_POLL_MAX_INTERVAL_S = 1200.0

TRIGGER_USAGE_MANUAL_REFRESH = "manual_refresh"
TRIGGER_USAGE_MANUAL_KEY = TRIGGER_USAGE_MANUAL_REFRESH
TRIGGER_USAGE_SERVICE_INTERVAL = "interval_10m"
USAGE_SAMPLE_SERVICE_INTERVAL_MODES = frozenset({"http"})


def jittered_usage_poll_interval_s(
    *,
    min_s: float = USAGE_SAMPLE_POLL_INTERVAL_S,
    max_s: float = USAGE_SAMPLE_POLL_MAX_INTERVAL_S,
) -> float:
    """Seconds until the next side-channel usage poll (10–20 minutes by default)."""
    low = min(min_s, max_s)
    high = max(min_s, max_s)
    return random.uniform(max(0.0, low), max(0.0, high))


def harness_usage_sample_payload(
    *,
    trigger: str,
    modes: set[str] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {"trigger": trigger}
    if modes is not None:
        payload["modes"] = sorted(modes)
    return payload


async def submit_harness_usage_sample_inprocess(
    db: RepoDb,
    *,
    repo_root: Path,
    trigger: str,
    modes: set[str] | None = None,
) -> dict[str, object] | None:
    """Sample directly from the feature service; no command/bus hop exists."""
    del trigger
    from murder.app.service.usage_sampling import sample_usage

    try:
        return await sample_usage(repo_root=repo_root, db=db, modes=modes)
    except Exception:
        log.exception("harness usage sample failed")
        return None


async def run_service_usage_poll_loop(
    repo_root: Path,
    db: RepoDb,
) -> None:
    """Sample http-mode harnesses immediately, then on a jittered interval until cancelled."""

    interval_modes = set(USAGE_SAMPLE_SERVICE_INTERVAL_MODES)
    while True:
        try:
            await submit_harness_usage_sample_inprocess(
                db,
                repo_root=repo_root,
                trigger=TRIGGER_USAGE_SERVICE_INTERVAL,
                modes=interval_modes,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("service usage poll loop iteration failed")
        await asyncio.sleep(jittered_usage_poll_interval_s())
