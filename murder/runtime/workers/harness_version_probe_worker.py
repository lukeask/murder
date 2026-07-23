"""Worker that probes installed harness CLI versions and updates the in-memory registry.

Pattern mirrors ``UsageProbeWorker``: the worker is event-driven (no poll loop);
``on_start`` fires the initial probe so the registry is populated before the
first crow is spawned.  Subsequent probes are triggered by the scheduler
(``state.harness_version.probe``) or a config change (``config.harnesses_changed``).

The registry reference is injected as a callback so this module stays decoupled
from ``Runtime``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from murder.runtime.orchestration.events import CommandEvent
from murder.runtime.orchestration.commands import OrchestrationCommand
from murder.runtime.orchestration.worker_names import WorkerName
from murder.llm.harnesses import REGISTRY
from murder.llm.harnesses.version_probe import (
    ProbeResult,
    binary_overrides_from_config,
    probe_all,
    probeable_kinds,
)
from murder.llm.harnesses.versioning import (
    HarnessVersionRecord,
    HarnessVersionRegistry,
    load_manifest,
    normalize_version,
    resolve_adapter_id,
)
from murder.runtime.workers.base import Worker, WorkerCommand, WorkerCtx, WorkerSpec
from murder.config import Config

_log = logging.getLogger(__name__)

RegistryUpdater = Callable[[list[HarnessVersionRecord]], None]


def _enabled_probeable_kinds(config: Config) -> list[str]:
    """Return harness kinds that are both enabled in config and have a --version command."""
    can_probe = set(probeable_kinds())
    crow_cfg = config.default_crow
    pool = list(crow_cfg.harnesses) if crow_cfg.harnesses else [crow_cfg.harness]
    collab_kind = config.collaborator.harness
    all_kinds = list(dict.fromkeys([*pool, collab_kind]))
    return [k for k in all_kinds if k in can_probe]


async def _run_probe(config: Config, updater: RegistryUpdater) -> dict[str, Any]:
    """Probe all enabled kinds and call *updater* with the resulting records."""
    kinds = _enabled_probeable_kinds(config)
    overrides = binary_overrides_from_config(config)
    manifest = load_manifest()

    results: list[ProbeResult] = await probe_all(kinds, binary_overrides=overrides)

    records: list[HarnessVersionRecord] = []
    for result in results:
        if result.raw is None:
            _log.debug("harness-version-probe: %s — version unavailable", result.kind)
            continue
        normalized = normalize_version(result.raw)
        adapter_id, verified = resolve_adapter_id(result.kind, normalized, manifest)
        record = HarnessVersionRecord(
            kind=result.kind,
            raw=result.raw,
            normalized=normalized,
            verified=verified,
            adapter_id=adapter_id,
            probed_at=datetime.now(tz=timezone.utc),
        )
        records.append(record)
        _log.info(
            "harness-version-probe: %s %s (adapter=%s, verified=%s)",
            result.kind,
            normalized,
            adapter_id,
            verified,
        )

    updater(records)
    return {
        "handled": True,
        "probed": [r.kind for r in records],
        "unavailable": [r.kind for r in results if r.raw is None],
    }


class HarnessVersionProbeWorker(Worker):
    COMMAND_KINDS = (
        OrchestrationCommand.STATE_HARNESS_VERSION_PROBE,
        OrchestrationCommand.CONFIG_HARNESSES_CHANGED,
    )

    def __init__(
        self,
        *,
        updater: RegistryUpdater,
        config: Config | None = None,
    ) -> None:
        super().__init__(
            WorkerSpec(
                name=WorkerName.HARNESS_VERSION_PROBE,
                accepts=self.COMMAND_KINDS,
                process_model="thread",
            )
        )
        self._updater = updater
        self._config = config

    @classmethod
    def from_runtime(cls, runtime: object) -> HarnessVersionProbeWorker:
        """Construct from a Runtime instance (preferred call site in bootstrap)."""
        registry: HarnessVersionRegistry = runtime.harness_versions  # type: ignore[union-attr]
        config: Config = runtime.config  # type: ignore[union-attr]
        return cls(updater=registry.replace, config=config)

    async def on_start(self, ctx: WorkerCtx) -> None:
        config = self._config or Config.load(ctx.repo_root)
        await _run_probe(config, self._updater)

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:
        await stop_event.wait()

    async def handle_command(self, command: WorkerCommand, ctx: WorkerCtx) -> bool:
        return command.name in self.COMMAND_KINDS

    async def on_command(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:
        if command.kind not in self.COMMAND_KINDS:
            return {"handled": False}
        config = self._config or Config.load(ctx.repo_root)
        return await _run_probe(config, self._updater)


__all__ = ["HarnessVersionProbeWorker"]
