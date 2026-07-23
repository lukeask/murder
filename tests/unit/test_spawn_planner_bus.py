"""Regression: `planner.spawn` rides the command bus like `crow.spawn_rogue`.

The Ink plans panel (`p` bind) submits `planner.spawn` with the effective planner
harness from settings; the worker forwards to ``spawn_planner_command`` →
``spawn_planner`` → ``ensure_planning_agent`` with harness/model/effort overrides.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from murder.runtime.orchestration.events import CommandEvent
from murder.runtime.orchestration.worker_names import WorkerName
from murder.runtime.orchestration.commands import OrchestrationCommand
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.workers.base import WorkerCtx
from murder.runtime.workers.orchestrator_worker import OrchestratorCommandWorker


def _planner_spawn_command(payload: dict[str, Any]) -> CommandEvent:
    return CommandEvent(
        id=uuid4(),
        run_id="run",
        target_worker=WorkerName.ORCHESTRATOR,
        kind=OrchestrationCommand.PLANNER_SPAWN,
        payload=payload,
        correlation_id="c",
        idempotency_key="i",
    )


@pytest.mark.asyncio
async def test_worker_forwards_planner_spawn_payload() -> None:
    seen: list[dict[str, Any]] = []

    async def spy_spawn_planner(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(payload)
        return {"handled": True, "agent_id": "planner-alpha"}

    async def _noop(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"handled": True}

    class _StubOrch:
        spawn_planner_command = staticmethod(spy_spawn_planner)

        def __getattr__(self, _name: str):
            return _noop

    worker = OrchestratorCommandWorker(_StubOrch())
    ctx = WorkerCtx(repo_root=Path("."))

    result = await worker.on_command(
        _planner_spawn_command(
            {"plan_name": "alpha", "harness": "codex", "model": "", "effort": "high"}
        ),
        ctx,
    )

    assert result == {"handled": True, "agent_id": "planner-alpha"}
    assert len(seen) == 1
    assert seen[0]["effort"] == "high"


@pytest.mark.asyncio
async def test_spawn_planner_command_forwards_harness_and_effort() -> None:
    orch = object.__new__(Orchestrator)
    captured: dict[str, Any] = {}

    async def spy_ensure(
        plan_name: str,
        *,
        harness: str | None = None,
        model: str | None = None,
        effort: str | None = None,
    ) -> str:
        captured.update(
            plan_name=plan_name,
            harness=harness,
            model=model,
            effort=effort,
        )
        return f"planner-{plan_name}"

    orch.ensure_planning_agent = spy_ensure  # type: ignore[method-assign]

    result = await orch.spawn_planner_command(
        {"plan_name": "demo", "harness": "codex", "model": "gpt-5.5", "effort": "high"}
    )

    assert result == {"handled": True, "agent_id": "planner-demo"}
    assert captured == {
        "plan_name": "demo",
        "harness": "codex",
        "model": "gpt-5.5",
        "effort": "high",
    }


@pytest.mark.asyncio
async def test_spawn_planner_command_rejects_missing_harness() -> None:
    orch = object.__new__(Orchestrator)

    async def unused_ensure(*_args: Any, **_kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("ensure_planning_agent should not be reached without harness")

    orch.ensure_planning_agent = unused_ensure  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="planner.spawn requires harness"):
        await orch.spawn_planner_command({"plan_name": "demo"})
