"""Live harness model catalog with a configured last-good fallback."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from murder.llm.harness_control.runtime.live_model_probe import (
    LIVE_MODEL_DISCOVERY_HARNESSES,
    probe_live_models,
)
from murder.llm.harnesses import REGISTRY
from murder.state.persistence.harness_models import upsert_harness_models

LOGGER = logging.getLogger(__name__)

CATALOG_PROVENANCE = "configured_catalog"
CATALOG_ADVISORY = "configured_catalog: static adapter catalog; not verified from a live harness"
DISCOVERY_TIMEOUT_S = 150.0
_CACHE: dict[str, list[tuple[str, str]]] = {}


def configured_harnesses() -> list[str]:
    """Return every registered harness represented by the configured catalog."""

    return sorted(REGISTRY)


def get_available_models(harness: str) -> list[tuple[str, str]]:
    """Return the live catalog when available, else a fresh static fallback."""

    cached = _CACHE.get(harness)
    if cached:
        return list(cached)
    adapter_cls = REGISTRY.get(harness)
    return list(adapter_cls.available_startup_models) if adapter_cls is not None else []


def clear_model_cache() -> None:
    """Drop process-local live results (primarily a test seam)."""

    _CACHE.clear()


async def _discover_one(
    harness: str, repo_root: Path, *, timeout_s: float
) -> tuple[list[tuple[str, str]], str | None]:
    try:
        result = await probe_live_models(harness, repo_root, timeout_s=timeout_s)
    except Exception as exc:  # noqa: BLE001 - one failed probe must not poison startup
        LOGGER.warning("live model discovery failed for %s", harness, exc_info=True)
        return [], f"live model discovery failed: {exc}"
    if result.ok and result.models:
        return list(result.models), None
    return [], result.message or "live model discovery returned no models"


async def refresh_and_persist_harness_models(
    repo_root: Path,
    db: sqlite3.Connection | None = None,
    *,
    timeout_s: float = DISCOVERY_TIMEOUT_S,
) -> None:
    """Probe interactive catalogs concurrently, cache successes, and persist."""

    live_harnesses = [
        harness for harness in configured_harnesses() if harness in LIVE_MODEL_DISCOVERY_HARNESSES
    ]
    discovered = await asyncio.gather(
        *(_discover_one(harness, repo_root, timeout_s=timeout_s) for harness in live_harnesses)
    )
    live_results = dict(zip(live_harnesses, discovered, strict=True))
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for harness in configured_harnesses():
        models, error = live_results.get(harness, ([], CATALOG_ADVISORY))
        if models:
            _CACHE[harness] = list(models)
        else:
            models = get_available_models(harness)
        if db is None:
            continue
        wire_models = [{"id": model_id, "label": label} for model_id, label in models]
        upsert_harness_models(
            db,
            harness=harness,
            models=wire_models,
            fetched_at=fetched_at,
            discovery_error=error,
        )
    if db is not None:
        db.commit()


__all__ = [
    "CATALOG_ADVISORY",
    "CATALOG_PROVENANCE",
    "DISCOVERY_TIMEOUT_S",
    "clear_model_cache",
    "configured_harnesses",
    "get_available_models",
    "refresh_and_persist_harness_models",
]
