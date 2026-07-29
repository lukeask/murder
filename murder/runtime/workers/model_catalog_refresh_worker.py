"""One-shot subprocess refresh of persisted direct-provider model catalogs."""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
from datetime import datetime, timezone
from typing import Any

from murder.llm.clients.catalog import get_provider_definition
from murder.runtime.orchestration.worker_names import WorkerName
from murder.runtime.workers.base import Worker, WorkerCtx, WorkerSpec
from murder.user_config import load_user_config, save_user_config

LOGGER = logging.getLogger(__name__)


async def refresh_provider_catalogs() -> dict[str, str]:
    """Reload config, refresh usable enabled built-ins, and preserve old caches on failure."""
    cfg = load_user_config()
    if cfg.llm is None:
        return {}
    outcomes: dict[str, str] = {}
    changed = False
    builtin_types = {"groq", "cerebras", "openrouter", "openai", "anthropic"}
    for provider_id, provider in cfg.llm.providers.items():
        if not provider.enabled or provider.type not in builtin_types:
            continue
        if provider.auth_source == "none":
            outcomes[provider_id] = "skipped: no credential source"
            continue
        definition = get_provider_definition(provider.type)
        if not definition.resolve_api_key(provider):
            outcomes[provider_id] = "skipped: credential unavailable"
            continue
        try:
            catalog = await definition.discover_models(provider)
        except Exception as exc:  # old known-good cache is intentionally retained
            provider.models.discovery_error = str(exc)
            outcomes[provider_id] = f"error: {exc}"
            changed = True
            continue
        provider.models.discovered = list(catalog)
        provider.models.discovery_error = None
        provider.models.discovered_at = datetime.now(timezone.utc).isoformat()
        outcomes[provider_id] = f"ok: {len(catalog)} models"
        changed = True
    if changed:
        save_user_config(cfg)
    return outcomes


def model_catalog_refresh_process_target(stop_event: Any, command_queue: Any) -> None:
    """Process target: exactly one startup refresh; no polling loop."""
    del command_queue
    if not stop_event.is_set():
        asyncio.run(refresh_provider_catalogs())
    # Keep the supervised child alive until shutdown, but never poll/refresh.
    stop_event.wait()


def _one_shot_refresh_process() -> None:
    asyncio.run(refresh_provider_catalogs())


def request_catalog_refresh() -> None:
    """Ask for an after-save refresh without coupling settings RPC latency to I/O."""
    process = mp.get_context("spawn").Process(
        target=_one_shot_refresh_process, name="model-catalog-refresh-save", daemon=True
    )
    process.start()


def provider_has_usable_credentials(provider: Any) -> bool:
    """Whether saving this provider warrants an after-save child refresh."""
    if not provider.enabled or provider.auth_source == "none":
        return False
    definition = get_provider_definition(provider.type or "")
    return bool(definition.resolve_api_key(provider)) or not definition.requires_api_key


class ModelCatalogRefreshWorker(Worker):
    """Supervisor registration for the catalog-refresh subprocess."""

    def __init__(self) -> None:
        super().__init__(
            WorkerSpec(
                name=WorkerName.MODEL_CATALOG_REFRESH,
                process_model="subprocess",
                shutdown_grace_s=0.5,
            )
        )

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # pragma: no cover
        await stop_event.wait()


__all__ = [
    "ModelCatalogRefreshWorker",
    "model_catalog_refresh_process_target",
    "provider_has_usable_credentials",
    "refresh_provider_catalogs",
    "request_catalog_refresh",
]
