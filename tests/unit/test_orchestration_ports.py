from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from murder.runtime.orchestration.command_repository import (
    PersistingCommandSubmitter,
    SqliteCommandRepository,
)
from murder.runtime.orchestration.commands import OrchestrationCommand
from murder.runtime.orchestration.events import CommandEvent
from murder.runtime.orchestration.notifier import InProcessOrchestrationEventSink
from murder.runtime.orchestration.worker_names import WorkerName
from murder.state.persistence.runs import insert_run
from murder.state.persistence.schema import get_db, init_db


def _command() -> CommandEvent:
    command_id = uuid4()
    return CommandEvent(
        id=command_id,
        run_id="run-ports",
        agent_id="scheduler",
        target_worker=WorkerName.ORCHESTRATOR,
        kind=OrchestrationCommand.SCHEDULER_KICKOFF_READY,
        correlation_id=str(command_id),
        idempotency_key=f"ports:{command_id}",
    )


@pytest.mark.asyncio
async def test_event_fanout_is_ephemeral_and_command_submission_is_durable(
    tmp_path: Path,
) -> None:
    connection = get_db(tmp_path / "state.db")
    init_db(connection)
    insert_run(connection, "run-ports", "{}")
    events = InProcessOrchestrationEventSink()
    observed: list[CommandEvent] = []

    async def observe(event: object) -> None:
        if isinstance(event, CommandEvent):
            observed.append(event)

    events.subscribe(observe)
    command = _command()

    await events.publish(command)
    assert connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 0

    submitter = PersistingCommandSubmitter(
        SqliteCommandRepository(connection),
        events,
    )
    await submitter.submit(command)

    assert connection.execute("SELECT COUNT(*) FROM commands").fetchone()[0] == 1
    assert observed == [command, command]
