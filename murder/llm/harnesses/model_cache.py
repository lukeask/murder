"""In-process cache of discovered harness models with classvar fallback.

The service process runs :func:`populate_model_cache` once at startup (after
``start_supervisor_workers``); it fires :func:`discover_harness_models` per
``model_discovery``-capable harness behind a graceful timeout and records the
result here. Reads go through :func:`get_available_models`, which returns the
discovered list when the cache is populated and otherwise falls back to the
adapter's hardcoded ``available_startup_models`` classvar.

The cache is process-local by design: the TUI runs in a separate process and
simply sees the classvar fallback. Cross-process delivery of discovered models
is handled by the generated ``HARNESSES_AND_MODELS.md`` doc and the bus RPCs,
not by this module.
"""

from __future__ import annotations

import asyncio
import logging
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


async def _discover_one(harness: str, repo_root: Path, *, timeout_s: float) -> None:
    try:
        result = await asyncio.wait_for(
            discover_harness_models(harness, repo_root), timeout=timeout_s
        )
    except (TimeoutError, asyncio.TimeoutError):
        LOGGER.info("model discovery for %s timed out; using fallback", harness)
        return
    except Exception:
        LOGGER.debug("model discovery for %s raised; using fallback", harness, exc_info=True)
        return
    if result.ok and result.data:
        set_discovered_models(harness, list(result.data))
        LOGGER.info("discovered %d models for %s", len(result.data), harness)
    else:
        LOGGER.info(
            "model discovery for %s failed (%s); using fallback",
            harness,
            result.message,
        )


async def populate_model_cache(
    repo_root: Path, *, timeout_s: float = DISCOVERY_TIMEOUT_S
) -> None:
    """Discover models for every enabled harness concurrently and cache them.

    Never raises and never blocks indefinitely: each per-harness probe is
    wrapped in its own timeout and all failures are swallowed so a hung or
    broken harness can't poison startup or the rest of the discovery batch.
    """
    harnesses = enabled_harnesses()
    if not harnesses:
        return
    await asyncio.gather(
        *(_discover_one(h, repo_root, timeout_s=timeout_s) for h in harnesses)
    )


__all__ = [
    "DISCOVERY_TIMEOUT_S",
    "clear_model_cache",
    "enabled_harnesses",
    "get_available_models",
    "populate_model_cache",
    "set_discovered_models",
]
