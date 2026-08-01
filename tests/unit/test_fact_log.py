from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
import turso
from pydantic import ValidationError

from murder.facts.contracts import (
    AggregateRef,
    FactActor,
    FactCorrelation,
    PrivateFactPayload,
    ProjectionInputDraft,
    RetainedFactDraft,
    WriterLeaseAcquiredPayload,
    fact_kind,
)
from murder.facts.log import (
    FactIdentityConflictError,
    FactLog,
    ProjectionInputLog,
    append_fact,
    append_projection_input,
    replay_facts,
    replay_projection_inputs,
)
from murder.state.persistence.connection import RepoDb
from tests.support.database import open_test_repo_db

NOW = datetime(2026, 7, 18, 15, 0, tzinfo=timezone.utc)


def _conn() -> RepoDb:
    return open_test_repo_db(Path(":memory:"))


def _draft() -> RetainedFactDraft:
    return RetainedFactDraft(
        fact_id=uuid4(),
        occurred_at=NOW,
        aggregate=AggregateRef(kind="ticket", id=uuid4(), revision=4),
        actor=FactActor(kind="workflow", id="delivery"),
        correlation=FactCorrelation(
            correlation_id=uuid4(),
            causation_id=uuid4(),
            trace_id=uuid4(),
        ),
        payload=PrivateFactPayload(
            kind="ticket.completed",
            data={"result": "done"},
        ),
    )


def test_retained_fact_draft_derives_kind_from_typed_payload() -> None:
    lease_payload = WriterLeaseAcquiredPayload(
        session_id=uuid4(),
        lease_id=uuid4(),
        mode="structured",
        fence=1,
        expires_at=NOW,
    )
    draft = RetainedFactDraft(
        occurred_at=NOW,
        actor=FactActor(kind="service", id="sessions"),
        correlation=FactCorrelation(correlation_id=uuid4()),
        payload=lease_payload,
    )
    assert draft.kind == "session.writer.acquired"
    assert fact_kind(lease_payload) == draft.kind

    with pytest.raises(ValidationError):
        RetainedFactDraft.model_validate(
            {
                "kind": "workflow.completed",
                "occurred_at": NOW,
                "actor": {"kind": "service", "id": "sessions"},
                "correlation": {"correlation_id": str(uuid4())},
                "payload": lease_payload.model_dump(mode="json"),
            }
        )

    with pytest.raises(ValidationError, match="registered as a public FactPayload"):
        PrivateFactPayload(
            kind="session.writer.acquired",
            data={"session_id": str(uuid4())},
        )


def test_authoritative_fact_schema_supports_independent_projection_inputs() -> None:
    db = _conn()
    conn = db.conn

    columns = {
        str(row["name"]): row
        for row in conn.execute("PRAGMA table_info(projection_inputs)").fetchall()
    }
    assert "input_id" in columns
    assert columns["source_fact_id"]["notnull"] == 0
    foreign_key = conn.execute(
        "PRAGMA foreign_key_list(projection_inputs)"
    ).fetchone()
    assert foreign_key["table"] == "retained_facts"
    assert foreign_key["on_delete"] == "RESTRICT"


def test_fact_and_projection_inputs_append_atomically_and_replay_by_cursor() -> None:
    db = _conn()
    draft = _draft()
    fact, inputs = append_fact(
        db,
        draft,
        projection_inputs=(
            ProjectionInputDraft(
                projection="schedule",
                subject_key=str(draft.aggregate.id),  # type: ignore[union-attr]
                generation=4,
            ),
        ),
        recorded_at=NOW,
    )

    assert fact.sequence == 1
    assert fact.fact_id == draft.fact_id
    assert len(inputs) == 1
    assert replay_facts(db, after_sequence=1) == ()
    assert replay_facts(db, after_sequence=0) == (fact,)
    assert (
        replay_projection_inputs(
            db,
            projection="schedule",
            after_sequence=inputs[0].sequence,
        )
        == ()
    )
    assert replay_projection_inputs(db, projection="schedule") == inputs


def test_projection_input_can_be_durable_without_inventing_a_fact() -> None:
    db = _conn()
    draft = ProjectionInputDraft(
        projection="activities",
        subject_key="activity-1",
        generation=4,
    )

    first = append_projection_input(db, draft, created_at=NOW)
    duplicate = append_projection_input(db, draft, created_at=NOW)

    assert duplicate == first
    assert first.source_fact_id is None
    assert replay_projection_inputs(db, projection="activities") == (first,)
    assert db.conn.execute("SELECT COUNT(*) AS n FROM retained_facts").fetchone()["n"] == 0


def test_fact_retry_is_idempotent_but_identity_reuse_with_new_content_fails() -> None:
    db = _conn()
    draft = _draft()
    invalidation = ProjectionInputDraft(
        projection="schedule",
        subject_key="ticket",
        generation=4,
    )
    first = append_fact(
        db,
        draft,
        projection_inputs=(invalidation,),
        recorded_at=NOW,
    )
    duplicate = append_fact(
        db,
        draft,
        projection_inputs=(invalidation,),
        recorded_at=NOW,
    )
    assert duplicate == first
    assert db.conn.execute("SELECT COUNT(*) FROM retained_facts").fetchone()[0] == 1
    assert db.conn.execute("SELECT COUNT(*) FROM projection_inputs").fetchone()[0] == 1

    offset_retry = append_fact(
        db,
        draft.model_copy(
            update={"occurred_at": NOW.astimezone(timezone(timedelta(hours=2)))}
        ),
        projection_inputs=(invalidation,),
        recorded_at=NOW.astimezone(timezone(timedelta(hours=2))),
    )
    assert offset_retry == first

    with pytest.raises(FactIdentityConflictError):
        append_fact(
            db,
            draft.model_copy(
                update={
                    "payload": PrivateFactPayload(
                        kind="ticket.completed",
                        data={"result": "different"},
                    )
                }
            ),
            recorded_at=NOW,
        )


def test_projection_input_failure_rolls_back_fact_and_database_rejects_mutation() -> None:
    db = _conn()
    draft = _draft()
    _install_rejecting_projection_trigger(db)
    with pytest.raises(turso.DatabaseError):
        append_fact(
            db,
            draft,
            projection_inputs=(
                ProjectionInputDraft(
                    projection="schedule",
                    subject_key="ticket",
                    generation=1,
                ),
                # Same uniqueness identity, but the insert itself is idempotent;
                # use a trigger to prove a downstream projection write failure
                # rolls the fact back with it.
                ProjectionInputDraft(
                    projection="forbidden",
                    subject_key="ticket",
                    generation=1,
                ),
            ),
            recorded_at=NOW,
        )
    assert db.conn.execute("SELECT COUNT(*) FROM retained_facts").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM projection_inputs").fetchone()[0] == 0

    fact, _ = append_fact(db, draft, recorded_at=NOW)
    with pytest.raises(turso.DatabaseError, match="retained facts are immutable"):
        db.conn.execute(
            "UPDATE retained_facts SET kind = 'changed' WHERE fact_id = ?",
            (str(fact.fact_id),),
        )


def _install_rejecting_projection_trigger(db: RepoDb) -> None:
    db.conn.execute(
        """
        CREATE TRIGGER reject_forbidden_projection
        BEFORE INSERT ON projection_inputs
        WHEN NEW.projection = 'forbidden'
        BEGIN
            SELECT RAISE(ABORT, 'forbidden projection');
        END
        """
    )


def test_only_retained_facts_are_visible_as_public_facts() -> None:
    db = _conn()
    assert replay_facts(db) == ()


def test_fact_cursor_reports_retention_gap_instead_of_reading_compatibility_events() -> None:
    db = _conn()
    first, _ = append_fact(db, _draft(), recorded_at=NOW)
    second, _ = append_fact(db, _draft(), recorded_at=NOW)
    facts = FactLog(db)
    assert facts.is_cursor_retained(0)
    db.conn.execute(
        "DELETE FROM retained_facts WHERE fact_id = ?",
        (str(first.fact_id),),
    )
    assert not facts.is_cursor_retained(0)
    assert facts.is_cursor_retained(second.sequence - 1)


def test_fact_and_projection_retention_are_pruned_independently() -> None:
    db = _conn()
    for generation in (1, 2):
        append_fact(
            db,
            _draft(),
            projection_inputs=(
                ProjectionInputDraft(
                    projection="activities",
                    subject_key=f"activity-{generation}",
                    generation=generation,
                ),
            ),
            recorded_at=NOW,
        )
    append_projection_input(
        db,
        ProjectionInputDraft(
            projection="activities",
            subject_key="activity-3",
            generation=3,
        ),
        created_at=NOW,
    )
    facts = FactLog(db, retention_min_records=1, retention_max_age_days=1)
    inputs = ProjectionInputLog(db, retention_min_records=1, retention_max_age_days=1)

    assert inputs.prune(now=NOW + timedelta(days=2)) == 2  # noqa: PLR2004
    assert facts.prune(now=NOW + timedelta(days=2)) == 1
    assert not inputs.is_cursor_retained(0)
    assert db.conn.execute("SELECT COUNT(*) AS n FROM projection_inputs").fetchone()["n"] == 1
    assert db.conn.execute("SELECT COUNT(*) AS n FROM retained_facts").fetchone()["n"] == 1
