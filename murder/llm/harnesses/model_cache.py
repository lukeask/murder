"""In-process cache of discovered harness models with classvar fallback.

The service process runs :func:`refresh_and_persist_harness_models` once at
startup (after ``start_supervisor_workers``); it fires
:func:`discover_harness_models` per ``model_discovery``-capable harness behind
a graceful timeout, records the result in the in-process cache, and persists
the per-harness rows to the SQLite DB so the Ink frontend can pull last-good
values via the ``state.harness_models_snapshot`` RPC.

Reads go through :func:`get_available_models`, which returns the discovered
list when the cache is populated and otherwise falls back to the adapter's
hardcoded ``available_startup_models`` classvar.

The cache is process-local by design: the TUI runs in a separate process and
simply sees the classvar fallback. Cross-process delivery of discovered models
is handled by the generated ``HARNESSES_AND_MODELS.md`` doc and the bus RPCs,
not by this module.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from murder.llm.harnesses import REGISTRY, capabilities_for
from murder.llm.harnesses.model_discovery import discover_harness_models

LOGGER = logging.getLogger(__name__)

# harness kind -> discovered [(model_id, label), ...]
_CACHE: dict[str, list[tuple[str, str]]] = {}

# Default per-harness budget for a single discovery probe. The probe itself has
# an internal ``ready_timeout_s`` but can still hang on session start-up, so we
# guard with our own wall-clock timeout and never let it block startup.
DISCOVERY_TIMEOUT_S: float = 60.0


def _fallback_models(harness: str) -> list[tuple[str, str]]:
    adapter_cls = REGISTRY.get(harness)
    if adapter_cls is None:
        return []
    return list(adapter_cls.available_startup_models)


def get_available_models(harness: str) -> list[tuple[str, str]]:
    """Return discovered models for *harness*, else the classvar fallback.

    Returns a fresh list of ``(model_id, label)`` tuples. When discovery has
    populated the cache for *harness* the discovered list is returned; otherwise
    the adapter's hardcoded ``available_startup_models`` is used.
    """
    cached = _CACHE.get(harness)
    if cached:
        return list(cached)
    return _fallback_models(harness)


def set_discovered_models(harness: str, models: list[tuple[str, str]]) -> None:
    """Record discovered models for *harness*. Empty lists are ignored so the
    accessor keeps falling back to the classvar."""
    if models:
        _CACHE[harness] = list(models)


def clear_model_cache() -> None:
    """Drop all cached discovery results (test helper / re-discovery)."""
    _CACHE.clear()


def enabled_harnesses() -> list[str]:
    """Harness kinds we can discover models for (``model_discovery`` capable)."""
    return [kind for kind in REGISTRY if capabilities_for(kind).model_discovery]


# ---------------------------------------------------------------------------
# Per-harness discovery with error capture (returns result tuple)
# ---------------------------------------------------------------------------

async def _discover_one_with_result(
    harness: str,
    repo_root: Path,
    *,
    timeout_s: float,
) -> tuple[list[tuple[str, str]], str | None]:
    """Run discovery for one harness and return ``(models, error_msg)``.

    *models* is the discovered list (may be empty on failure).
    *error_msg* is None on success, non-None on any failure/timeout.
    Never raises.
    """
    error: str | None = None
    try:
        result = await asyncio.wait_for(
            discover_harness_models(harness, repo_root), timeout=timeout_s
        )
    except (TimeoutError, asyncio.TimeoutError):
        LOGGER.info("model discovery for %s timed out; using fallback", harness)
        return [], "timeout"
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("model discovery for %s raised; using fallback", harness, exc_info=True)
        return [], str(exc) or "unknown error"
    if result.ok and result.data:
        models = list(result.data)
        LOGGER.info("discovered %d models for %s", len(models), harness)
        return models, None
    else:
        msg = result.message or "discovery returned no models"
        LOGGER.info("model discovery for %s failed (%s); using fallback", harness, msg)
        error = msg
        return [], error


async def refresh_and_persist_harness_models(
    repo_root: Path,
    db: sqlite3.Connection | None = None,
    *,
    timeout_s: float = DISCOVERY_TIMEOUT_S,
) -> None:
    """Discover models for all enabled harnesses, update the in-process cache,
    and persist results to the SQLite DB if *db* is provided.

    This is the canonical single-pass discovery function.  It replaces the
    ``populate_model_cache`` call at startup so discovery fires exactly once.
    Each per-harness probe runs concurrently; failures are captured in
    ``discovery_error`` and never poison the batch.  The in-process cache
    (``_CACHE``) is updated on success so callers using ``get_available_models``
    see fresh data immediately.

    *db* — an open SQLite connection (from ``get_db``/``init_db``). When None
    the DB write is skipped (useful in tests that only exercise the cache).
    """
    harnesses = enabled_harnesses()
    if not harnesses:
        return

    tasks = [
        _discover_one_with_result(h, repo_root, timeout_s=timeout_s)
        for h in harnesses
    ]
    results = await asyncio.gather(*tasks)

    fetched_at = datetime.utcnow().isoformat(timespec="seconds")

    for harness, (models, error) in zip(harnesses, results):
        # Update in-process cache for successes.
        if models:
            set_discovered_models(harness, models)

        # Persist to DB if provided.
        if db is not None:
            try:
                from murder.state.persistence.harness_models import upsert_harness_models

                # Models to persist: use discovered if available, else fallback.
                persist_models = models if models else [
                    {"id": mid, "label": label}
                    for mid, label in _fallback_models(harness)
                ]
                # If discovery succeeded, models came as tuples; convert to dicts.
                if models:
                    persist_models = [{"id": mid, "label": label} for mid, label in models]

                upsert_harness_models(
                    db,
                    harness=harness,
                    models=persist_models,
                    fetched_at=fetched_at,
                    discovery_error=error,
                )
                db.commit()
            except Exception:  # noqa: BLE001
                LOGGER.debug(
                    "failed to persist model discovery for %s to DB", harness, exc_info=True
                )


async def populate_model_cache(
    repo_root: Path, *, timeout_s: float = DISCOVERY_TIMEOUT_S
) -> None:
    """Discover models for every enabled harness concurrently and cache them.

    Kept for backward compatibility.  New callers that also need DB persistence
    should use :func:`refresh_and_persist_harness_models` directly.

    Never raises and never blocks indefinitely.
    """
    await refresh_and_persist_harness_models(repo_root, db=None, timeout_s=timeout_s)


__all__ = [
    "DISCOVERY_TIMEOUT_S",
    "clear_model_cache",
    "enabled_harnesses",
    "get_available_models",
    "populate_model_cache",
    "refresh_and_persist_harness_models",
    "set_discovered_models",
]
