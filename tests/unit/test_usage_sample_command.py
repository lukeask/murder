from __future__ import annotations

import asyncio

import pytest

from murder import usage_sample_command
from murder.bus.protocol import CommandEvent
from murder.runtime.workers.base import WorkerCtx
from murder.runtime.workers.usage_probe_worker import UsageProbeWorker


def test_jittered_usage_poll_interval_uses_20_percent_bounds(monkeypatch) -> None:
    calls: list[tuple[float, float]] = []

    def _uniform(low: float, high: float) -> float:
        calls.append((low, high))
        return high

    monkeypatch.setattr(usage_sample_command.random, "uniform", _uniform)

    interval = usage_sample_command.jittered_usage_poll_interval_s()

    assert calls == [(480.0, 720.0)]
    assert interval == 720.0


def test_harness_usage_sample_payload_includes_modes_when_set() -> None:
    payload = usage_sample_command.harness_usage_sample_payload(
        trigger=usage_sample_command.TRIGGER_USAGE_SERVICE_INTERVAL,
        modes={"http"},
    )
    assert payload == {
        "trigger": usage_sample_command.TRIGGER_USAGE_SERVICE_INTERVAL,
        "modes": ["http"],
    }


def test_harness_usage_sample_payload_omits_modes_when_none() -> None:
    payload = usage_sample_command.harness_usage_sample_payload(
        trigger=usage_sample_command.TRIGGER_USAGE_MANUAL_REFRESH,
    )
    assert payload == {"trigger": usage_sample_command.TRIGGER_USAGE_MANUAL_REFRESH}


@pytest.mark.asyncio
async def test_service_usage_poll_loop_samples_before_first_sleep(monkeypatch) -> None:
    order: list[str] = []
    sample_kwargs: list[dict[str, object]] = []

    async def _submit(*_args, **kwargs) -> dict[str, object]:
        order.append("sample")
        sample_kwargs.append(kwargs)
        return {}

    async def _sleep(_delay: float) -> None:
        order.append("sleep")
        raise asyncio.CancelledError

    monkeypatch.setattr(
        usage_sample_command,
        "submit_harness_usage_sample_inprocess",
        _submit,
    )
    monkeypatch.setattr(usage_sample_command.asyncio, "sleep", _sleep)

    with pytest.raises(asyncio.CancelledError):
        await usage_sample_command.run_service_usage_poll_loop(
            broker=object(),  # type: ignore[arg-type]
            db=object(),  # type: ignore[arg-type]
            run_id="run-test",
        )

    assert order == ["sample", "sleep"]
    assert sample_kwargs[0]["modes"] == {"http"}
    assert sample_kwargs[0]["trigger"] == usage_sample_command.TRIGGER_USAGE_SERVICE_INTERVAL


@pytest.mark.asyncio
async def test_usage_probe_worker_rejects_scheduler_probe_usage_alias() -> None:
    worker = UsageProbeWorker()

    assert "scheduler.probe_usage" not in worker.COMMAND_KINDS

    handled = await worker.handle_command(
        type("Cmd", (), {"name": "scheduler.probe_usage"})(),  # type: ignore[arg-type]
        WorkerCtx(repo_root=__import__("pathlib").Path("/tmp")),  # type: ignore[arg-type]
    )
    assert handled is False

    cmd = CommandEvent(
        run_id="run-test",
        agent_id="tester",
        target_worker="usage-probe",
        kind="scheduler.probe_usage",
        payload={},
        correlation_id="c1",
        idempotency_key="k1",
    )
    result = await worker.on_command(cmd, WorkerCtx(repo_root=__import__("pathlib").Path("/tmp")))  # type: ignore[arg-type]
    assert result == {"handled": False}
