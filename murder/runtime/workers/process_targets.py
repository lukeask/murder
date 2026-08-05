from __future__ import annotations

import asyncio
import os
import queue
from pathlib import Path
from typing import Any

from murder.app.service.command_dispatch import CommandDispatcher
from murder.config import Config, load_repo_env, merge_subprocess_env
from murder.llm.harnesses.usage_sampling import (
    UsageSamplingContext,
    harness_kinds_to_sample,
    sample_harness_usages,
)
from murder.runtime.orchestration.events import CommandEvent
from murder.runtime.workers.base import WorkerCommand, WorkerCtx
from murder.runtime.workers.usage_probe_worker import UsageProbeWorker
from murder.state.persistence.connection import open_repo_db


def usage_probe_process_target(
    stop_event: Any, command_queue: Any, repo_root: str, run_id: str
) -> None:
    asyncio.run(_run_usage_probe_process(stop_event, command_queue, repo_root, run_id))


async def _run_usage_probe_process(
    stop_event: Any,
    command_queue: Any,
    repo_root_raw: str,
    run_id: str,
) -> None:
    repo_root = Path(repo_root_raw)
    # Dedicated per-repo child: materialize daemon baseline + repo overlay into
    # this process environ so nested harness/CLI calls inherit project secrets.
    # Mutation stays local to this spawned worker (never the daemon).
    os.environ.update(merge_subprocess_env(load_repo_env(repo_root)))
    cfg = Config.load(repo_root)
    db = open_repo_db(repo_root)
    sampling = UsageSamplingContext(config=cfg, repo_root=repo_root, db=db)

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

    worker = UsageProbeWorker(sampler=_sample, kinds_provider=_kinds)
    # Private orchestration signals deliberately do not cross process
    # boundaries.  The application socket serves a fresh projection from the
    # authoritative usage tables, so this worker needs no bus instance.
    ctx = WorkerCtx(repo_root=repo_root, db=db, run_id=run_id)
    dispatcher = CommandDispatcher(db=db, repo_root=repo_root)
    try:
        while not stop_event.is_set():
            try:
                item = command_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            except Exception:
                continue
            command_id = getattr(item, "command_id", None)
            command = getattr(item, "event", item)
            if isinstance(command, WorkerCommand):
                continue
            if not isinstance(command, CommandEvent):
                continue
            if command_id is None:
                command_id = str(command.id)
            try:
                result = await worker.on_command(command, ctx)
            except Exception as exc:  # noqa: BLE001
                # An exception may declare itself non-retryable (e.g.
                # WorktreeError): a deterministic failure that would fail
                # identically on retry overrides the command's retry policy so
                # we fail fast to escalation. Mirrors supervisor._run_command.
                retryable = command.retryable and getattr(exc, "retryable", True)
                dispatcher.fail(
                    command_id=str(command_id),
                    last_error=str(exc),
                    retryable=retryable,
                )
                continue
            dispatcher.finish(
                command_id=str(command_id),
                command=command,
                worker_name=worker.spec.name,
                result=result,
            )
    finally:
        db.close()
