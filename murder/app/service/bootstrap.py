"""Worker registration for the service supervisor."""

from __future__ import annotations

from pathlib import Path

from murder.app.service.command_dispatch import CommandDispatcher
from murder.app.service.supervisor import Supervisor
from murder.llm.harnesses.versioning import HarnessVersionRegistry
from murder.observability.advanced_log import AdvancedLogBase
from murder.runtime.agent_runtime import AgentRuntime
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.runtime.orchestration.ports import CommandSubmitter, OrchestrationEventSink
from murder.runtime.scheduler import SchedulerWorker
from murder.runtime.workers import (
    CodebaseMapWorker,
    CollaboratorWorker,
    DoneSessionSweeperWorker,
    HarnessVersionProbeWorker,
    ModelCatalogRefreshWorker,
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
    harness_versions: HarnessVersionRegistry,
    agents: AgentRuntime,
    orchestrator: Orchestrator,
) -> Supervisor:
    """Start all service workers on a shared supervisor."""
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
    await supervisor.start_worker(StateCommandWorker())
    await supervisor.start_worker(
        SchedulerWorker(command_submitter=commands, events=events)
    )
    await supervisor.start_worker(UsageProbeWorker.from_worker_ctx(worker_ctx))
    await supervisor.start_worker(ModelCatalogRefreshWorker())
    await supervisor.start_worker(
        HarnessVersionProbeWorker(updater=harness_versions.replace)
    )
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
    return supervisor


__all__ = ["start_supervisor_workers"]
