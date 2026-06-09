"""Regression: `effort` rides the spawn bus end-to-end (C11 / was B10).

The renderer-agnostic backend must carry an `effort` value supplied at the
`crow.spawn_rogue` RPC boundary all the way to `spawn_rogue(effort=…)` (which
forwards it to the harness adapter as `startup_effort`). These tests pin the two
backend hops that the future Ink spawn wizard will feed:

  RPC ingress payload  →  OrchestratorCommandWorker.on_command  →  spawn callable
  spawn_rogue_command(payload)  →  Orchestrator.spawn_rogue(effort=…)

The frontend hop (Textual `SpawnWizard` / `app._do_spawn_rogue`) intentionally
drops effort; that frontend is being replaced by the Ink wizard per the plan.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from murder.bus.protocol import CommandEvent
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.workers.base import WorkerCtx
from murder.runtime.workers.orchestrator_worker import OrchestratorCommandWorker


def _spawn_command(payload: dict[str, Any]) -> CommandEvent:
    return CommandEvent(
        id=uuid4(),
        run_id="run",
        target_worker="orchestrator",
        kind="crow.spawn_rogue",
        payload=payload,
        correlation_id="c",
        idempotency_key="i",
    )


@pytest.mark.asyncio
async def test_worker_forwards_full_spawn_payload_including_effort() -> None:
    """The worker hands the spawn callable the payload verbatim — effort survives."""
    seen: list[dict[str, Any]] = []

    async def spy_spawn_rogue(payload: dict[str, Any]) -> dict[str, Any]:
        seen.append(payload)
        return {"handled": True, "agent_id": "crow-rogue-x"}

    async def _noop(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"handled": True}

    worker = OrchestratorCommandWorker(
        kickoff_ready=_noop,
        apply_carve_ready=_noop,
        capture_submit=_noop,
        retry_failed=_noop,
        set_schedule_at=_noop,
        update_metadata=_noop,
        force_status=_noop,
        note_ensure=_noop,
        note_retire=_noop,
        send_agent_message=_noop,
        send_agent_key=_noop,
        refresh_agent_transcript=_noop,
        interrupt_agent=_noop,
        stop_agent=_noop,
        rename_rogue=_noop,
        scaffold_plan=_noop,
        rename_plan=_noop,
        deprecate_plan=_noop,
        quick_kick_ticket=_noop,
        quick_create_ticket=lambda _title: {"handled": True},
        spawn_rogue=spy_spawn_rogue,
        reconfigure_collaborator=_noop,
    )
    ctx = WorkerCtx(repo_root=Path("."))

    result = await worker.on_command(
        _spawn_command(
            {"harness": "claude_code", "model": "claude-opus-4-8", "effort": "high"}
        ),
        ctx,
    )

    assert result == {"handled": True, "agent_id": "crow-rogue-x"}
    assert len(seen) == 1
    assert seen[0]["effort"] == "high"


@pytest.mark.asyncio
async def test_spawn_rogue_command_passes_effort_to_spawn_rogue() -> None:
    """`spawn_rogue_command` unpacks `effort` and forwards it to `spawn_rogue`."""
    # Build an Orchestrator without its heavy __init__ deps; we only exercise the
    # pure payload-unpacking method, with the real spawn replaced by a spy.
    orch = object.__new__(Orchestrator)
    captured: dict[str, Any] = {}

    async def spy_spawn_rogue(
        harness: str,
        model: str,
        effort: str | None = None,
        name: str | None = None,
        *,
        worktree_path: str | None = None,
        worktree_branch: str | None = None,
    ) -> str:
        captured.update(
            harness=harness,
            model=model,
            effort=effort,
            name=name,
            worktree_path=worktree_path,
            worktree_branch=worktree_branch,
        )
        return "crow-rogue-y"

    orch.spawn_rogue = spy_spawn_rogue  # type: ignore[method-assign]

    result = await orch.spawn_rogue_command(
        {"harness": "codex", "model": "gpt-5.3-codex", "effort": "xhigh"}
    )

    assert result == {"handled": True, "agent_id": "crow-rogue-y"}
    assert captured["effort"] == "xhigh"


@pytest.mark.asyncio
async def test_spawn_rogue_command_rejects_non_string_effort() -> None:
    """Effort validation guards the RPC boundary."""
    orch = object.__new__(Orchestrator)

    async def unused_spawn_rogue(*_args: Any, **_kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("spawn_rogue should not be reached on bad effort")

    orch.spawn_rogue = unused_spawn_rogue  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="effort must be a string"):
        await orch.spawn_rogue_command(
            {"harness": "codex", "model": "gpt-5.3-codex", "effort": 3}
        )


# --- H4 (F11): required-field guard, pinned on the Python side ----------------
# These pin the live handler's REQUIRED fields so the Ink contract test
# (inktui/test/components/SpawnWizardModal.test.tsx — "H4 — spawn payload
# contract") and this handler can never drift apart silently. The Ink side
# asserts the payload always carries `harness` + `model`; here we assert the
# handler actually rejects payloads that omit them.


@pytest.mark.asyncio
async def test_spawn_rogue_command_rejects_missing_harness() -> None:
    """Required field guard: a payload without `harness` is rejected before spawn."""
    orch = object.__new__(Orchestrator)

    async def unused_spawn_rogue(*_args: Any, **_kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("spawn_rogue should not be reached without harness")

    orch.spawn_rogue = unused_spawn_rogue  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="requires harness"):
        await orch.spawn_rogue_command({"model": "sonnet"})


@pytest.mark.asyncio
async def test_spawn_rogue_command_rejects_missing_model() -> None:
    """Required field guard: a payload without `model` is rejected before spawn."""
    orch = object.__new__(Orchestrator)

    async def unused_spawn_rogue(*_args: Any, **_kwargs: Any) -> str:  # pragma: no cover
        raise AssertionError("spawn_rogue should not be reached without model")

    orch.spawn_rogue = unused_spawn_rogue  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="requires model"):
        await orch.spawn_rogue_command({"harness": "claude"})
