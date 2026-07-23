"""Worker registration for the service supervisor."""

from __future__ import annotations

from pathlib import Path

from murder.app.service.command_dispatch import CommandDispatcher
from murder.app.service.runtime import Runtime
from murder.app.service.supervisor import Supervisor
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.orchestration.ports import OrchestrationEventSink
from murder.runtime.scheduler import SchedulerWorker
from murder.runtime.workers import (
    CodebaseMapWorker,
    CollaboratorWorker,
    DoneSessionSweeperWorker,
    HarnessVersionProbeWorker,
    OrchestratorCommandWorker,
    PlannerSessionSweeperWorker,
    StateCommandWorker,
    UsageProbeWorker,
    WorkerCtx,
)


async def start_supervisor_workers(
    *,
    repo_root: Path,
    runtime: Runtime,
    orchestrator: Orchestrator,
    events: OrchestrationEventSink,
) -> Supervisor:
    """Start all service workers on a shared supervisor."""
    worker_ctx = WorkerCtx(
        repo_root=repo_root,
        db=runtime.db,
        run_id=runtime.run_id,
    )
    cmd_dispatcher = (
        CommandDispatcher(
            conn=runtime.db,
            repo_root=repo_root,
            events=events,
            advanced_log=runtime.advanced_log,
        )
        if runtime.db is not None
        else None
    )
    supervisor = Supervisor(worker_ctx, command_dispatcher=cmd_dispatcher)
    await supervisor.start_worker(StateCommandWorker())
    await supervisor.start_worker(
        SchedulerWorker(command_submitter=runtime.command_submitter, events=events)
    )
    await supervisor.start_worker(UsageProbeWorker.from_worker_ctx(worker_ctx))
    await supervisor.start_worker(HarnessVersionProbeWorker.from_runtime(runtime))
    await supervisor.start_worker(DoneSessionSweeperWorker())
    await supervisor.start_worker(PlannerSessionSweeperWorker())
    await supervisor.start_worker(
        CollaboratorWorker(
            ensure_collaborator=orchestrator.ensure_collaborator,
            get_agent=runtime.get_agent,
        )
    )
    await supervisor.start_worker(OrchestratorCommandWorker(orchestrator))
    await supervisor.start_worker(CodebaseMapWorker())
    return supervisor


__all__ = ["start_supervisor_workers"]
