from __future__ import annotations

import asyncio
import queue
from types import SimpleNamespace
from typing import Any

from murder.bus import Bus
from murder.bus.protocol import CommandEvent
from murder.config import Config
from murder.llm.harnesses.usage_sampling import (
    UsageSamplingContext,
    harness_kinds_to_sample,
    sample_harness_usages,
)
from murder.state.persistence.schema import get_db
from murder.app.service.command_dispatch import CommandDispatcher
from murder.state.storage.paths import db_path
from murder.runtime.workers.base import WorkerCommand, WorkerCtx
from murder.runtime.workers.usage_probe_worker import UsageProbeWorker


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
    from pathlib import Path

    repo_root = Path(repo_root_raw)
    cfg = Config.load(repo_root)
    conn = get_db(db_path(repo_root))
    sampling = UsageSamplingContext(config=cfg, repo_root=repo_root, db=conn)

    async def _sample(_ctx: WorkerCtx) -> tuple[int, int]:
        return await sample_harness_usages(sampling)

    def _kinds(_ctx: WorkerCtx) -> list[str]:
        return harness_kinds_to_sample(sampling)

    worker = UsageProbeWorker(sampler=_sample, kinds_provider=_kinds)
    # F1 (queue_row chunk): give the subprocess its own DB-backed bus so the
    # usage-snapshot emit in ``UsageProbeWorker.on_command`` actually reaches the
    # client. The parent supervisor never injects a bus into this subprocess
    # (``_start_subprocess_runner`` -> fire-and-forget ``runner.dispatch``), and
    # ``Bus.publish`` persists to the shared ``events`` table before fan-out, which
    # the client tails (``DurableBroker.tail``) -- so a child-constructed ``Bus``
    # on the same DB delivers cross-process. Without this, the emit would no-op.
    bus = Bus(run_id, conn)
    ctx = WorkerCtx(repo_root=repo_root, db=conn, run_id=run_id, bus=bus)
    dispatcher = CommandDispatcher(conn=conn, repo_root=repo_root)
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
        conn.close()
