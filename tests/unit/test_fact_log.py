from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

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
    ensure_fact_schema,
    replay_facts,
    replay_projection_inputs,
)
from murder.state.persistence.schema import init_db

NOW = datetime(2026, 7, 18, 15, 0, tzinfo=timezone.utc)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)
    return conn


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
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_fact_schema(conn)

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
    conn = _conn()
    draft = _draft()
    fact, inputs = append_fact(
        conn,
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
    assert replay_facts(conn, after_sequence=1) == ()
    assert replay_facts(conn, after_sequence=0) == (fact,)
    assert (
        replay_projection_inputs(
            conn,
            projection="schedule",
            after_sequence=inputs[0].sequence,
        )
        == ()
    )
    assert replay_projection_inputs(conn, projection="schedule") == inputs


def test_projection_input_can_be_durable_without_inventing_a_fact() -> None:
    conn = _conn()
    draft = ProjectionInputDraft(
        projection="activities",
        subject_key="activity-1",
        generation=4,
    )

    first = append_projection_input(conn, draft, created_at=NOW)
    duplicate = append_projection_input(conn, draft, created_at=NOW)

    assert duplicate == first
    assert first.source_fact_id is None
    assert replay_projection_inputs(conn, projection="activities") == (first,)
    assert conn.execute("SELECT COUNT(*) AS n FROM retained_facts").fetchone()["n"] == 0


def test_fact_retry_is_idempotent_but_identity_reuse_with_new_content_fails() -> None:
    conn = _conn()
    draft = _draft()
    invalidation = ProjectionInputDraft(
        projection="schedule",
        subject_key="ticket",
        generation=4,
    )
    first = append_fact(
        conn,
        draft,
        projection_inputs=(invalidation,),
        recorded_at=NOW,
    )
    duplicate = append_fact(
        conn,
        draft,
        projection_inputs=(invalidation,),
        recorded_at=NOW,
    )
    assert duplicate == first
    assert conn.execute("SELECT COUNT(*) FROM retained_facts").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM projection_inputs").fetchone()[0] == 1

    offset_retry = append_fact(
        conn,
        draft.model_copy(
            update={"occurred_at": NOW.astimezone(timezone(timedelta(hours=2)))}
        ),
        projection_inputs=(invalidation,),
        recorded_at=NOW.astimezone(timezone(timedelta(hours=2))),
    )
    assert offset_retry == first

    with pytest.raises(FactIdentityConflictError):
        append_fact(
            conn,
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
    conn = _conn()
    draft = _draft()
    _install_rejecting_projection_trigger(conn)
    with pytest.raises(sqlite3.IntegrityError):
        append_fact(
            conn,
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
    assert conn.execute("SELECT COUNT(*) FROM retained_facts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM projection_inputs").fetchone()[0] == 0

    fact, _ = append_fact(conn, draft, recorded_at=NOW)
    with pytest.raises(sqlite3.IntegrityError, match="retained facts are immutable"):
        conn.execute(
            "UPDATE retained_facts SET kind = 'changed' WHERE fact_id = ?",
            (str(fact.fact_id),),
        )


def _install_rejecting_projection_trigger(conn: sqlite3.Connection) -> None:
    conn.execute(
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
    conn = _conn()
    assert replay_facts(conn) == ()


def test_fact_cursor_reports_retention_gap_instead_of_reading_compatibility_events() -> None:
    conn = _conn()
    first, _ = append_fact(conn, _draft(), recorded_at=NOW)
    second, _ = append_fact(conn, _draft(), recorded_at=NOW)
    facts = FactLog(conn)
    assert facts.is_cursor_retained(0)
    conn.execute(
        "DELETE FROM retained_facts WHERE fact_id = ?",
        (str(first.fact_id),),
    )
    assert not facts.is_cursor_retained(0)
    assert facts.is_cursor_retained(second.sequence - 1)


def test_fact_and_projection_retention_are_pruned_independently() -> None:
    conn = _conn()
    for generation in (1, 2):
        append_fact(
            conn,
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
        conn,
        ProjectionInputDraft(
            projection="activities",
            subject_key="activity-3",
            generation=3,
        ),
        created_at=NOW,
    )
    facts = FactLog(conn, retention_min_records=1, retention_max_age_days=1)
    inputs = ProjectionInputLog(conn, retention_min_records=1, retention_max_age_days=1)

    assert inputs.prune(now=NOW + timedelta(days=2)) == 2  # noqa: PLR2004
    assert facts.prune(now=NOW + timedelta(days=2)) == 1
    assert not inputs.is_cursor_retained(0)
    assert conn.execute("SELECT COUNT(*) AS n FROM projection_inputs").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM retained_facts").fetchone()["n"] == 1
