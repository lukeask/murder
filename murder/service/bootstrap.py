"""Worker registration for the service supervisor."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from murder.orchestration.orchestrator import Orchestrator
from murder.scheduler import SchedulerWorker
from murder.service.command_dispatch import CommandDispatcher
from murder.service.runtime import Runtime
from murder.service.supervisor import Supervisor
from murder.workers import (
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
    await supervisor.start_worker(
        OrchestratorCommandWorker(
            kickoff_ready=orchestrator.kickoff_ready,
            apply_carve_ready=orchestrator.apply_ticket_carve_ready,
            capture_submit=orchestrator.submit_notetaker_capture,
            retry_failed=orchestrator.retry_failed_ticket,
            set_schedule_at=orchestrator.set_schedule_at,
            update_metadata=orchestrator.update_ticket_metadata,
            force_status=orchestrator.force_ticket_status,
            note_ensure=orchestrator.ensure_note,
            note_retire=orchestrator.retire_note,
            send_agent_message=orchestrator.send_agent_message,
            send_agent_key=orchestrator.send_agent_key,
            interrupt_agent=orchestrator.interrupt_agent,
            stop_agent=orchestrator.stop_agent,
            rename_rogue=orchestrator.rename_rogue_agent,
            scaffold_plan=orchestrator.scaffold_plan,
            rename_plan=orchestrator.rename_plan,
            deprecate_plan=orchestrator.deprecate_plan,
            quick_kick_ticket=orchestrator.quick_kick_ticket,
            spawn_rogue=orchestrator.spawn_rogue_command,
        )
    )
    return supervisor


__all__ = ["start_supervisor_workers"]
