from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from murder.bus.protocol import CommandEvent
from murder.config import Config
from murder.harnesses.usage_sampling import (
    UsageSamplingContext,
    harness_kinds_to_sample,
    sample_harness_usages,
    sample_harness_usages_for_config,
)
from murder.harnesses.usage_sampling import _RuntimeDbScope
from murder.workers.base import Worker, WorkerCommand, WorkerCtx, WorkerSpec

UsageSampler = Callable[[WorkerCtx], Awaitable[tuple[int, int]]]
KindsProvider = Callable[[WorkerCtx], list[str]]


async def _missing_sampler(ctx: WorkerCtx) -> tuple[int, int]:  # pragma: no cover
    del ctx
    raise RuntimeError("UsageProbeWorker requires a sampler")


class UsageProbeWorker(Worker):
    COMMAND_KINDS = ("state.harness_usage.sample", "scheduler.probe_usage")

    def __init__(
        self,
        *,
        sampler: UsageSampler = _missing_sampler,
        kinds_provider: KindsProvider | None = None,
    ) -> None:
        super().__init__(
            WorkerSpec(
                name="usage-probe",
                accepts=self.COMMAND_KINDS,
                process_model="subprocess",
            )
        )
        self._sampler = sampler
        self._kinds_provider = kinds_provider or (lambda _ctx: [])

    @classmethod
    def from_worker_ctx(cls, ctx: WorkerCtx) -> UsageProbeWorker:
        """Build probe worker from explicit worker context (preferred)."""
        if ctx.db is None:
            raise RuntimeError("UsageProbeWorker requires ctx.db")
        cfg = Config.load(ctx.repo_root)
        sampling = UsageSamplingContext(config=cfg, repo_root=ctx.repo_root, db=ctx.db)

        async def _sample(_ctx: WorkerCtx) -> tuple[int, int]:
            return await sample_harness_usages(sampling)

        def _kinds(_ctx: WorkerCtx) -> list[str]:
            return harness_kinds_to_sample(sampling)

        return cls(sampler=_sample, kinds_provider=_kinds)

    @classmethod
    def from_runtime(
        cls,
        runtime: _RuntimeDbScope,
        *,
        sampler: Callable[
            [_RuntimeDbScope], Awaitable[tuple[int, int]]
        ] = sample_harness_usages_for_config,
    ) -> UsageProbeWorker:
        """Thin shim for call sites still holding a config/db/repo scope."""

        async def _sample(_ctx: WorkerCtx) -> tuple[int, int]:
            return await sampler(runtime)

        def _kinds(_ctx: WorkerCtx) -> list[str]:
            return harness_kinds_to_sample(UsageSamplingContext.from_runtime(runtime))

        return cls(sampler=_sample, kinds_provider=_kinds)

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # noqa: ARG002
        await stop_event.wait()

    async def handle_command(self, command: WorkerCommand, ctx: WorkerCtx) -> bool:  # noqa: ARG002
        return command.name in self.COMMAND_KINDS

    async def on_command(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:
        if command.kind not in self.COMMAND_KINDS:
            return {"handled": False}
        sampled_kinds = self._kinds_provider(ctx)
        stored, failures = await self._sampler(ctx)
        return {
            "handled": True,
            "stored": stored,
            "failures": failures,
            "sampled_kinds": sampled_kinds,
        }
