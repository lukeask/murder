"""High-risk acceptance coverage for shared-database repository partitions."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from murder.app.service.read_model import ServiceReadModel
from murder.app.service.schedule_snapshot import build_schedule_snapshot
from murder.contracts.common import Correlation, PrincipalKind
from murder.roster.repository import RosterRepository
from murder.roster.service import RosterService
from murder.state.persistence import notes, plans, reports
from murder.state.persistence.activities import insert_activity_requests, list_activities
from murder.state.persistence.agents import append_agent_message, get_agent_messages
from murder.state.persistence.commands import get_worker_heartbeat, upsert_worker_heartbeat
from murder.state.persistence.connection import RepoDb
from murder.state.persistence.conversation import upsert_conversation
from murder.state.persistence.history import get_status_map, set_history_status
from murder.state.persistence.triggers import create_trigger, list_triggers
from murder.state.persistence.workflow_runs import create_workflow_run, list_workflow_runs
from murder.work.notes.sync import NoteSync
from murder.work.plans.schema import Plan
from murder.work.plans.sync import PlanSync
from murder.work.reports.sync import ReportSync
from murder.work.tickets.sync import TicketSync
from murder.work.triggers.runtime import ManualTrigger, StartWorkflowTarget, TriggerRecord
from murder.work.workflows.runtime import (
    ActivityRequestDraft,
    ExecutionRequirements,
    PrincipalRef,
    RunAgentTurnActivity,
    StageRunState,
    StageStatus,
    StaticDagWorkflowStateV1,
    VersionedState,
    WorkflowRunRecord,
    WorkflowStatus,
    versioned_state,
)
from tests.support.database import (
    SECOND_TEST_REPOSITORY_ID,
    open_test_repo_db,
)

NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _plan(name: str, body: str) -> Plan:
    return Plan(name=name, created_at=NOW, updated_at=NOW, body=body)


def _seed_ticket(db: RepoDb, *, title: str) -> None:
    db.conn.execute(
        """
        INSERT INTO tickets(repository_id, id, title, status, created_at, updated_at)
        VALUES (?, 't001', ?, 'ready', ?, ?)
        """,
        (db.repository_id, title, NOW.isoformat(), NOW.isoformat()),
    )


def _workflow() -> WorkflowRunRecord:
    state: VersionedState = versioned_state(
        StaticDagWorkflowStateV1(
            inputs={"source": "partition-isolation"},
            stages=(StageRunState(stage_id="work", status=StageStatus.READY),),
        ),
        schema_name="static_dag",
        schema_version=1,
    )
    return WorkflowRunRecord(
        workflow_id=uuid4(),
        definition_name="partition-isolation",
        definition_version=1,
        status=WorkflowStatus.RUNNING,
        revision=0,
        state=state,
        created_at=NOW,
        updated_at=NOW,
        started_by=PrincipalRef(kind=PrincipalKind.SERVICE, id="acceptance-test"),
        correlation=Correlation(correlation_id=uuid4()),
        parent_ticket_id="t001",
    )


def _seed_runtime_rows(db: RepoDb, *, label: str) -> tuple[str, str, str]:
    run = _workflow()
    create_workflow_run(db, run)
    activity_id = uuid4()
    insert_activity_requests(
        db,
        workflow_id=run.workflow_id,
        workflow_revision=1,
        drafts=(
            ActivityRequestDraft(
                activity_id=activity_id,
                payload=RunAgentTurnActivity(
                    instructions=label,
                    requirements=ExecutionRequirements(),
                ),
                idempotency_key=f"activity:{label}",
            ),
        ),
        created_at=NOW,
    )
    trigger = TriggerRecord(
        trigger_id=uuid4(),
        name=label,
        version=1,
        spec=ManualTrigger(command=label),
        target=StartWorkflowTarget(definition_name="partition-isolation", definition_version=1),
        created_at=NOW,
    )
    create_trigger(db, trigger)
    upsert_conversation(
        db,
        conversation_id=f"conversation-{label}",
        agent_id=f"agent-{label}",
        harness="codex",
        model="test",
    )
    return str(run.workflow_id), str(activity_id), str(trigger.trigger_id)


def test_partitioned_materialization_and_read_paths(tmp_path: Path) -> None:
    """Rows from the other partition never become local files or read results."""
    shared = tmp_path / "shared.db"
    primary = open_test_repo_db(shared)
    secondary = open_test_repo_db(shared, repository_id=SECOND_TEST_REPOSITORY_ID)
    primary_root = tmp_path / "primary"
    secondary_root = tmp_path / "secondary"
    primary_root.mkdir()
    secondary_root.mkdir()
    try:
        for db, label in ((primary, "primary"), (secondary, "secondary")):
            _seed_ticket(db, title=f"{label} ticket")
            plans.upsert_plan(
                db,
                _plan("shared-plan", f"{label} plan"),
                content_hash=hashlib.sha256(label.encode()).hexdigest(),
                materialized_path=".murder/plans/shared-plan.md",
            )
            plans.upsert_plan(
                db,
                _plan(f"{label}-only-plan", label),
                content_hash=label,
                materialized_path=f".murder/plans/{label}-only-plan.md",
            )
            notes.upsert_note(
                db,
                "shared-note",
                body=f"{label} note",
                materialized_path=".murder/notes/shared-note.md",
            )
            notes.upsert_note(
                db,
                f"{label}-only-note",
                body=label,
                materialized_path=f".murder/notes/{label}-only-note.md",
            )
            reports.upsert_report(
                db,
                "shared-report",
                body=f"{label} report",
                materialized_path=".murder/reports/shared-report.md",
            )
            reports.upsert_report(
                db,
                f"{label}-only-report",
                body=label,
                materialized_path=f".murder/reports/{label}-only-report.md",
            )
            db.conn.execute(
                """
                INSERT INTO agents(repository_id, agent_id, role, ticket_id, status, started_at)
                VALUES (?, ?, 'crow', 't001', 'running', ?)
                """,
                (db.repository_id, f"agent-{label}", NOW.isoformat()),
            )

        primary_workflow_id, primary_activity_id, _ = _seed_runtime_rows(primary, label="primary")
        _seed_runtime_rows(secondary, label="secondary")

        asyncio.run(PlanSync(primary_root, primary).reconcile_all())
        asyncio.run(TicketSync(primary_root, primary).reconcile_all())
        asyncio.run(NoteSync(primary_root, primary).reconcile_all())
        asyncio.run(ReportSync(primary_root, primary).reconcile_all())

        assert "primary plan" in (primary_root / ".murder/plans/shared-plan.md").read_text()
        assert "primary ticket" in (primary_root / ".murder/tickets/t001.md").read_text()
        assert (primary_root / ".murder/notes/shared-note.md").read_text() == "primary note"
        assert (primary_root / ".murder/reports/shared-report.md").read_text() == "primary report"
        for path in (
            ".murder/plans/secondary-only-plan.md",
            ".murder/notes/secondary-only-note.md",
            ".murder/reports/secondary-only-report.md",
        ):
            assert not (primary_root / path).exists()

        read_model = ServiceReadModel(primary, primary_root)
        assert [row.name for row in read_model.get_plans_snapshot().plans] == [
            "primary-only-plan",
            "shared-plan",
        ]
        assert [row.name for row in read_model.get_notes_snapshot().notes] == [
            "primary-only-note",
            "shared-note",
        ]
        assert [row.name for row in read_model.get_reports_snapshot().reports] == [
            "primary-only-report",
            "shared-report",
        ]
        conversations = read_model.get_conversations_snapshot().conversations
        assert [row.conversation_id for row in conversations] == ["conversation-primary"]
        assert [row.agent_id for row in RosterRepository().snapshot(primary).sessions] == [
            "agent-primary"
        ]
        assert [row["agent_id"] for row in RosterService(primary).get()["sessions"]] == [
            "agent-primary"
        ]
        schedule = build_schedule_snapshot(primary, as_of=NOW, invalidation_key="test")
        assert [row.title for row in schedule.active_tickets] == ["primary ticket"]
        workflow_ids = [str(row.workflow_id) for row in list_workflow_runs(primary)]
        assert workflow_ids == [primary_workflow_id]
        assert [str(row.activity_id) for row in list_activities(primary)] == [primary_activity_id]
        assert [row.name for row in list_triggers(primary)] == ["primary"]
    finally:
        primary.close()
        secondary.close()


def test_every_application_table_has_a_nondefault_partition_key(tmp_path: Path) -> None:
    """The schema cannot add an unpartitioned application table by accident."""
    db = open_test_repo_db(tmp_path / "shared.db")
    try:
        tables = [
            str(row["name"])
            for row in db.conn.execute(
                """
                SELECT name FROM sqlite_master
                 WHERE type = 'table'
                   AND name NOT LIKE 'sqlite_%'
                   AND name NOT LIKE '__turso_internal_%'
                """
            ).fetchall()
            if row["name"] != "repositories"
        ]
        assert tables
        for table in tables:
            columns = db.conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            repository = next((row for row in columns if row["name"] == "repository_id"), None)
            assert repository is not None, table
            assert int(repository["notnull"]) == 1, table
            assert repository["dflt_value"] is None, table
            primary_key_is_partitioned = int(repository["pk"]) == 1
            index_is_partitioned = any(
                (parts := db.conn.execute(f'PRAGMA index_info("{index["name"]}")').fetchall())
                and parts[0]["name"] == "repository_id"
                for index in db.conn.execute(f'PRAGMA index_list("{table}")').fetchall()
            )
            assert primary_key_is_partitioned or index_is_partitioned, table
    finally:
        db.close()


def test_deterministic_runtime_ids_are_partition_local(tmp_path: Path) -> None:
    """Ticket-derived runtime ids may repeat without cross-repo collisions."""
    shared = tmp_path / "shared.db"
    primary = open_test_repo_db(shared)
    secondary = open_test_repo_db(shared, repository_id=SECOND_TEST_REPOSITORY_ID)
    try:
        for index, db in enumerate((primary, secondary), start=1):
            _seed_ticket(db, title=f"repo {index}")
            db.conn.execute(
                "INSERT INTO runs(repository_id, run_id, started_at, config_snapshot) "
                "VALUES (?, ?, ?, '{}')",
                (db.repository_id, f"run-{index}", NOW.isoformat()),
            )
            db.conn.execute(
                "INSERT INTO agents(repository_id, agent_id, role, ticket_id, status, started_at) "
                "VALUES (?, 'crow-t001', 'crow', 't001', 'running', ?)",
                (db.repository_id, NOW.isoformat()),
            )
            db.conn.execute(
                "INSERT INTO agents(repository_id, agent_id, role, status, started_at) "
                "VALUES (?, 'planner-shared', 'planner', 'running', ?)",
                (db.repository_id, NOW.isoformat()),
            )
            append_agent_message(db, "crow-t001", "user", f"message {index}")
            upsert_conversation(
                db, conversation_id="crow-t001", agent_id="crow-t001"
            )
            set_history_status(db, "crow-t001:0", "dismissed", f"repo {index}")
            upsert_worker_heartbeat(
                db, worker_id="orchestrator", run_id=f"run-{index}", payload={"n": 1}
            )
            upsert_worker_heartbeat(
                db, worker_id="orchestrator", run_id=f"run-{index}", payload={"n": 2}
            )

        assert get_agent_messages(primary, "crow-t001")[0]["body"] == "message 1"
        assert get_agent_messages(secondary, "crow-t001")[0]["body"] == "message 2"
        assert get_status_map(primary)["crow-t001:0"][1] == "repo 1"
        assert get_status_map(secondary)["crow-t001:0"][1] == "repo 2"
        primary_heartbeat = get_worker_heartbeat(primary, "orchestrator")
        secondary_heartbeat = get_worker_heartbeat(secondary, "orchestrator")
        assert primary_heartbeat is not None
        assert secondary_heartbeat is not None
        assert primary_heartbeat["payload_json"] == '{"n": 2}'
        assert secondary_heartbeat["run_id"] == "run-2"
    finally:
        primary.close()
        secondary.close()
