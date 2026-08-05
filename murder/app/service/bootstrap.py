"""Worker registration for the per-repository service supervisor.

User-global workers (``ModelCatalogRefreshWorker``, ``HarnessVersionProbeWorker``)
are owned by ``DaemonHost`` so a multi-host process does not race them.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from murder.app.service.command_dispatch import CommandDispatcher
from murder.app.service.supervisor import Supervisor
from murder.observability.advanced_log import AdvancedLogBase
from murder.runtime.agent_runtime import AgentRuntime
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.orchestration.ports import CommandSubmitter, OrchestrationEventSink
from murder.runtime.scheduler import SchedulerWorker
from murder.runtime.workers import (
    CodebaseMapWorker,
    CollaboratorWorker,
    DoneSessionSweeperWorker,
    OrchestratorCommandWorker,
    PlannerSessionSweeperWorker,
    StateCommandWorker,
    UsageProbeWorker,
    WorkerCtx,
)
from murder.state.persistence.connection import RepoDb


async def start_supervisor_workers(
    *,
    repo_root: Path,
    db: RepoDb,
    run_id: str,
    events: OrchestrationEventSink,
    commands: CommandSubmitter,
    advanced_log: AdvancedLogBase,
    agents: AgentRuntime,
    orchestrator: Orchestrator,
) -> Supervisor:
    """Start per-repository workers on a shared supervisor."""
    worker_ctx = WorkerCtx(
        repo_root=repo_root,
        db=db,
        run_id=run_id,
    )
    cmd_dispatcher = CommandDispatcher(
        db=db,
        repo_root=repo_root,
        events=events,
        advanced_log=advanced_log,
    )
    supervisor = Supervisor(worker_ctx, command_dispatcher=cmd_dispatcher)
    try:
        await supervisor.start_worker(StateCommandWorker())
        await supervisor.start_worker(
            SchedulerWorker(command_submitter=commands, events=events)
        )
        await supervisor.start_worker(UsageProbeWorker.from_worker_ctx(worker_ctx))
        await supervisor.start_worker(DoneSessionSweeperWorker())
        await supervisor.start_worker(PlannerSessionSweeperWorker())
        await supervisor.start_worker(
            CollaboratorWorker(
                ensure_collaborator=orchestrator.ensure_collaborator,
                get_agent=agents.find,
            )
        )
        await supervisor.start_worker(OrchestratorCommandWorker(orchestrator))
        await supervisor.start_worker(CodebaseMapWorker())
    except BaseException:
        with contextlib.suppress(Exception):
            await supervisor.stop_all()
        raise
    return supervisor


__all__ = ["start_supervisor_workers"]
