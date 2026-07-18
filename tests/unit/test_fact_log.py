from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from murder.bus import Bus
from murder.bus.broker import DurableBroker
from murder.facts.contracts import (
    AggregateRef,
    FactActor,
    FactCorrelation,
    ProjectionInputDraft,
    RetainedFactDraft,
)
from murder.facts.log import (
    FactIdentityConflictError,
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
        kind="ticket.completed",
        occurred_at=NOW,
        aggregate=AggregateRef(kind="ticket", id=uuid4(), revision=4),
        actor=FactActor(kind="workflow", id="delivery"),
        correlation=FactCorrelation(
            correlation_id=uuid4(),
            causation_id=uuid4(),
            trace_id=uuid4(),
        ),
        payload={"result": "done"},
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
            draft.model_copy(update={"payload": {"result": "different"}}),
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


def test_compatibility_events_are_never_visible_as_public_facts() -> None:
    conn = _conn()
    conn.execute(
        """
        INSERT INTO runs(run_id, started_at, config_snapshot)
        VALUES ('run', ?, '{}')
        """,
        (NOW.isoformat(),),
    )
    conn.execute(
        """
        INSERT INTO events(
            ts, run_id, agent_id, role, ticket_id, type, schema_version, payload_json
        ) VALUES (?, 'run', '', '', NULL, 'command', 1, '{}')
        """,
        (NOW.isoformat(),),
    )
    assert replay_facts(conn) == ()


def test_fact_cursor_reports_retention_gap_instead_of_reading_compatibility_events() -> None:
    conn = _conn()
    first, _ = append_fact(conn, _draft(), recorded_at=NOW)
    second, _ = append_fact(conn, _draft(), recorded_at=NOW)
    broker = DurableBroker(Bus("run", conn), conn)
    assert broker.is_fact_cursor_retained(0)
    conn.execute(
        "DELETE FROM retained_facts WHERE fact_id = ?",
        (str(first.fact_id),),
    )
    assert not broker.is_fact_cursor_retained(0)
    assert broker.is_fact_cursor_retained(second.sequence - 1)


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
    broker = DurableBroker(
        Bus("run", conn),
        conn,
        retention_min_events=1,
        retention_max_age_days=1,
    )

    assert broker.prune_projection_inputs(now=NOW + timedelta(days=2)) == 2  # noqa: PLR2004
    assert broker.prune_retained_facts(now=NOW + timedelta(days=2)) == 1
    assert not broker.is_projection_cursor_retained(0)
    assert conn.execute("SELECT COUNT(*) AS n FROM projection_inputs").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM retained_facts").fetchone()["n"] == 1
