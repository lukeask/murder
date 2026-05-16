from __future__ import annotations

import asyncio
import queue
from types import SimpleNamespace
from typing import Any

from murder import db as dbmod
from murder.config import Config
from murder.harnesses.usage_sampling import (
    harness_kinds_to_sample,
    sample_harness_usages_for_config,
)
from murder.storage.paths import db_path
from murder.workers.base import WorkerCtx
from murder.workers.base import WorkerCommand
from murder.workers.usage_probe_worker import UsageProbeWorker


def usage_probe_process_target(stop_event: Any, command_queue: Any, repo_root: str, run_id: str) -> None:
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
    conn = dbmod.connect(db_path(repo_root))
    runtime_like = SimpleNamespace(config=cfg, repo_root=repo_root, db=conn)

    async def _sample(_ctx: WorkerCtx) -> tuple[int, int]:
        return await sample_harness_usages_for_config(runtime_like)  # type: ignore[arg-type]

    def _kinds(_ctx: WorkerCtx) -> list[str]:
        return harness_kinds_to_sample(runtime_like)  # type: ignore[arg-type]

    worker = UsageProbeWorker(sampler=_sample, kinds_provider=_kinds)
    ctx = WorkerCtx(repo_root=repo_root, db=conn, run_id=run_id)
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
            if command_id is None:
                command_id = str(getattr(command, "id", ""))
            try:
                result = await worker.on_command(command, ctx)
            except Exception as exc:  # noqa: BLE001
                if command_id:
                    dbmod.fail_command(
                        conn,
                        command_id=str(command_id),
                        last_error=str(exc),
                        retryable=bool(getattr(command, "retryable", True)),
                    )
                continue
            if command_id:
                if result.get("handled") is False:
                    dbmod.fail_command(
                        conn,
                        command_id=str(command_id),
                        last_error=f"worker {worker.spec.name!r} did not handle {command.kind!r}",
                        retryable=False,
                    )
                else:
                    dbmod.complete_command(conn, command_id=str(command_id), result=result)
    finally:
        conn.close()
