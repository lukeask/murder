"""Worker registration for the service supervisor."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.scheduler import SchedulerWorker
from murder.app.service.command_dispatch import CommandDispatcher
from murder.app.service.runtime import Runtime
from murder.app.service.supervisor import Supervisor
from murder.runtime.workers import (
    CollaboratorWorker,
    DoneSessionSweeperWorker,
    HarnessVersionProbeWorker,
    OrchestratorCommandWorker,
    StateCommandWorker,
    UsageProbeWorker,
    WorkerCtx,
)

if TYPE_CHECKING:
    from murder.bus.broker import DurableBroker


async def start_supervisor_workers(
    *,
    repo_root: Path,
    runtime: Runtime,
    orchestrator: Orchestrator,
    broker: DurableBroker,
) -> Supervisor:
    """Start all service workers on a shared supervisor."""
    worker_ctx = WorkerCtx(
        repo_root=repo_root,
        db=runtime.db,
        bus=broker,
        run_id=runtime.run_id,
    )
    cmd_dispatcher = (
        CommandDispatcher(conn=runtime.db, repo_root=repo_root, bus=broker)
        if runtime.db is not None
        else None
    )
    supervisor = Supervisor(worker_ctx, command_dispatcher=cmd_dispatcher)
    await supervisor.start_worker(StateCommandWorker())
    await supervisor.start_worker(SchedulerWorker())
    await supervisor.start_worker(UsageProbeWorker.from_worker_ctx(worker_ctx))
    await supervisor.start_worker(HarnessVersionProbeWorker.from_runtime(runtime))
    await supervisor.start_worker(DoneSessionSweeperWorker())
    await supervisor.start_worker(
        CollaboratorWorker(
            ensure_collaborator=orchestrator.ensure_collaborator,
            get_agent=runtime.get_agent,
        )
    )
    await supervisor.start_worker(OrchestratorCommandWorker(orchestrator))
    return supervisor


__all__ = ["start_supervisor_workers"]
