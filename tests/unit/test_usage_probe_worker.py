from __future__ import annotations

from pathlib import Path

import pytest

from murder.bus.protocol import CommandEvent
from murder.workers.base import WorkerCommand, WorkerCtx
from murder.workers.usage_probe_worker import UsageProbeWorker


@pytest.mark.asyncio
async def test_usage_probe_worker_returns_sampling_payload() -> None:
    ctx = WorkerCtx(repo_root=Path("."))

    async def _sampler(got_ctx: WorkerCtx) -> tuple[int, int]:
        assert got_ctx is ctx
        return 3, 1

    worker = UsageProbeWorker(
        sampler=_sampler,
        kinds_provider=lambda got_ctx: ["cursor", "claude_code"] if got_ctx is ctx else [],
    )
    result = await worker.on_command(
        CommandEvent(
            run_id="r1",
            target_worker="usage-probe",
            kind="state.harness_usage.sample",
            correlation_id="c1",
            idempotency_key="i1",
        ),
        ctx,
    )

    assert result == {
        "handled": True,
        "stored": 3,
        "failures": 1,
        "sampled_kinds": ["cursor", "claude_code"],
    }


@pytest.mark.asyncio
async def test_usage_probe_worker_rejects_unknown_command() -> None:
    async def _sampler(ctx: WorkerCtx) -> tuple[int, int]:  # pragma: no cover
        del ctx
        raise AssertionError("sampler should not be called")

    worker = UsageProbeWorker(sampler=_sampler, kinds_provider=lambda _ctx: ["cursor"])
    result = await worker.on_command(
        CommandEvent(
            run_id="r1",
            target_worker="usage-probe",
            kind="scheduler.other",
            correlation_id="c1",
            idempotency_key="i1",
        ),
        WorkerCtx(repo_root=Path(".")),
    )

    assert result == {"handled": False}


@pytest.mark.asyncio
async def test_usage_probe_worker_spec_and_handle_command() -> None:
    worker = UsageProbeWorker(sampler=_impossible_sampler)

    assert worker.spec.name == "usage-probe"
    assert worker.spec.process_model == "subprocess"
    assert worker.spec.accepts == (
        "state.harness_usage.sample",
        "scheduler.probe_usage",
    )

    assert await worker.handle_command(
        WorkerCommand(name="state.harness_usage.sample"),
        WorkerCtx(repo_root=Path(".")),
    )
    assert await worker.handle_command(
        WorkerCommand(name="scheduler.probe_usage"),
        WorkerCtx(repo_root=Path(".")),
    )
    assert not await worker.handle_command(
        WorkerCommand(name="scheduler.other"),
        WorkerCtx(repo_root=Path(".")),
    )


async def _impossible_sampler(rt) -> tuple[int, int]:  # pragma: no cover
    del rt
    raise AssertionError("sampler should not be called in this test")
