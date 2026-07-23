from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from murder.runtime.orchestration.events import CommandEvent
from murder.runtime.orchestration.commands import OrchestrationCommand
from murder.runtime.orchestration.worker_names import WorkerName
from murder.runtime.scheduler.projection import invalidate_schedule
from murder.config import Config
from murder.llm.harnesses.usage_sampling import (
    UsageSamplingContext,
    harness_kinds_to_sample,
    sample_harness_usages,
)
from murder.runtime.workers.base import Worker, WorkerCommand, WorkerCtx, WorkerSpec

UsageSampler = Callable[..., Awaitable[tuple[int, int]]]
KindsProvider = Callable[..., list[str]]


def _modes_from_payload(payload: dict[str, Any]) -> set[str] | None:
    raw = payload.get("modes")
    if raw is None:
        return None
    if isinstance(raw, (list, set, frozenset, tuple)):
        return {str(mode) for mode in raw}
    return None


async def _missing_sampler(
    ctx: WorkerCtx,
    *,
    modes: set[str] | None = None,
) -> tuple[int, int]:  # pragma: no cover
    del ctx, modes
    raise RuntimeError("UsageProbeWorker requires a sampler")


class UsageProbeWorker(Worker):
    COMMAND_KINDS = (OrchestrationCommand.STATE_HARNESS_USAGE_SAMPLE,)

    def __init__(
        self,
        *,
        sampler: UsageSampler = _missing_sampler,
        kinds_provider: KindsProvider | None = None,
    ) -> None:
        super().__init__(
            WorkerSpec(
                name=WorkerName.USAGE_PROBE,
                accepts=self.COMMAND_KINDS,
                process_model="subprocess",
                # Usage sampling is disposable; a hung sample must never delay
                # supervisor shutdown (which holds the repo flock). Fall through
                # to terminate/kill quickly instead of waiting the 2s default.
                shutdown_grace_s=0.2,
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

        async def _sample(
            _ctx: WorkerCtx,
            *,
            modes: set[str] | None = None,
        ) -> tuple[int, int]:
            return await sample_harness_usages(sampling, modes=modes)

        def _kinds(
            _ctx: WorkerCtx,
            *,
            modes: set[str] | None = None,
        ) -> list[str]:
            return harness_kinds_to_sample(sampling, modes=modes)

        return cls(sampler=_sample, kinds_provider=_kinds)

    async def run(self, ctx: WorkerCtx, stop_event: asyncio.Event) -> None:  # noqa: ARG002
        await stop_event.wait()

    async def handle_command(self, command: WorkerCommand, ctx: WorkerCtx) -> bool:  # noqa: ARG002
        return command.name in self.COMMAND_KINDS

    async def on_command(self, command: CommandEvent, ctx: WorkerCtx) -> dict[str, Any]:
        if command.kind not in self.COMMAND_KINDS:
            return {"handled": False}
        modes = _modes_from_payload(command.payload)
        sampled_kinds = self._kinds_provider(ctx, modes=modes)
        stored, failures = await self._sampler(ctx, modes=modes)
        # Usage rows feed the schedule projection; append durable inputs instead
        # of publishing a generic runtime notification.
        if stored > 0 and ctx.db is not None:
            for kind in sampled_kinds:
                invalidate_schedule(ctx.db, subject_key=f"usage:{kind}")
        return {
            "handled": True,
            "stored": stored,
            "failures": failures,
            "sampled_kinds": sampled_kinds,
        }
