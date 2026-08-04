"""Regression coverage for repository-scoped deterministic persistence ids."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from murder.facts.contracts import ProjectionInputDraft
from murder.facts.log import append_projection_input, replay_projection_inputs
from murder.runtime.orchestration.notifier import InProcessOrchestrationEventSink
from murder.runtime.orchestration.structured_decisions import StructuredDecisionRouter
from murder.state.persistence.connection import Connection, RepoDb
from murder.state.persistence.schema import init_db
from tests.support.database import SECOND_TEST_REPOSITORY_ID, open_test_repo_db


def test_projection_input_id_collision_is_isolated_by_repository(tmp_path: Path) -> None:
    """Deterministic invalidations from equal local state both persist."""
    db = open_test_repo_db(tmp_path / "shared.db")
    other = RepoDb(conn=db.conn, repository_id=SECOND_TEST_REPOSITORY_ID)
    input_id = UUID("01234567-89ab-4cde-8fab-0123456789ab")
    draft = ProjectionInputDraft(
        input_id=input_id,
        projection="schedule",
        subject_key="t001",
        generation=0,
    )

    first = append_projection_input(db, draft)
    second = append_projection_input(other, draft)

    assert first.input_id == second.input_id == input_id
    assert first.sequence != second.sequence
    assert replay_projection_inputs(db, projection="schedule") == (first,)
    assert replay_projection_inputs(other, projection="schedule") == (second,)


def test_structured_decision_id_collision_is_isolated_by_repository(tmp_path: Path) -> None:
    """The router's deterministic request ids remain durable per repository."""
    db = open_test_repo_db(tmp_path / "shared.db")
    other = RepoDb(conn=db.conn, repository_id=SECOND_TEST_REPOSITORY_ID)
    request_id = "01234567-89ab-4cde-8fab-0123456789ab"
    first = StructuredDecisionRouter(
        db=db,
        events=InProcessOrchestrationEventSink(),
        run_id="run",
        get_agent=lambda _agent_id: None,
    )
    second = StructuredDecisionRouter(
        db=other,
        events=InProcessOrchestrationEventSink(),
        run_id="run",
        get_agent=lambda _agent_id: None,
    )

    first._record_request(  # noqa: SLF001 - verifies the router's persistence boundary
        request_id=request_id,
        agent_id="crow-t001",
        decision_kind="question",
        request_identity="same-observation",
        request={"repository": "first"},
    )
    second._record_request(  # noqa: SLF001 - verifies the router's persistence boundary
        request_id=request_id,
        agent_id="crow-t001",
        decision_kind="question",
        request_identity="same-observation",
        request={"repository": "second"},
    )

    first_request = first._load_request(request_id)  # noqa: SLF001
    second_request = second._load_request(request_id)  # noqa: SLF001
    assert first_request is not None
    assert second_request is not None
    assert first_request["request"] == {"repository": "first"}
    assert second_request["request"] == {"repository": "second"}
    assert (
        db.conn.execute(
            "SELECT COUNT(*) FROM structured_decisions WHERE decision_request_id = ?",
            (request_id,),
        ).fetchone()[0]
        == 2
    )


def test_partition_identity_migration_preserves_consolidated_rows(tmp_path: Path) -> None:
    """A v2 consolidated database upgrades without dropping either table's rows."""
    db = open_test_repo_db(tmp_path / "shared.db")
    conn = db.conn
    _install_v2_global_identity_tables(conn)
    conn.execute(
        """
        INSERT INTO structured_decisions(
            repository_id, decision_request_id, agent_id, decision_kind,
            request_identity, request_json, created_at
        ) VALUES (?, ?, 'crow-t001', 'question', 'identity', '{}', '2026-08-01T00:00:00+00:00')
        """,
        (db.repository_id, "first-request"),
    )
    conn.execute(
        """
        INSERT INTO structured_decisions(
            repository_id, decision_request_id, agent_id, decision_kind,
            request_identity, request_json, created_at
        ) VALUES (?, ?, 'crow-t001', 'question', 'identity', '{}', '2026-08-01T00:00:00+00:00')
        """,
        (SECOND_TEST_REPOSITORY_ID, "second-request"),
    )
    conn.execute(
        """
        INSERT INTO projection_inputs(
            input_id, repository_id, source_fact_id, projection, subject_key, generation, created_at
        ) VALUES (?, ?, NULL, 'schedule', 't001', 0, '2026-08-01T00:00:00+00:00')
        """,
        ("first-input", db.repository_id),
    )
    conn.execute(
        """
        INSERT INTO projection_inputs(
            input_id, repository_id, source_fact_id, projection, subject_key, generation, created_at
        ) VALUES (?, ?, NULL, 'schedule', 't001', 1, '2026-08-01T00:00:00+00:00')
        """,
        ("second-input", SECOND_TEST_REPOSITORY_ID),
    )
    conn.execute("PRAGMA user_version = 2")

    init_db(conn)

    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT repository_id, decision_request_id FROM structured_decisions ORDER BY decision_request_id"
        ).fetchall()
    ] == [
        (db.repository_id, "first-request"),
        (SECOND_TEST_REPOSITORY_ID, "second-request"),
    ]
    assert [
        tuple(row)
        for row in conn.execute(
            "SELECT repository_id, input_id FROM projection_inputs ORDER BY input_id"
        ).fetchall()
    ] == [
        (db.repository_id, "first-input"),
        (SECOND_TEST_REPOSITORY_ID, "second-input"),
    ]
    primary_keys = conn.execute("PRAGMA table_info(structured_decisions)").fetchall()
    assert [
        row["name"] for row in sorted(primary_keys, key=lambda row: row["pk"]) if row["pk"]
    ] == [
        "repository_id",
        "decision_request_id",
    ]


def _install_v2_global_identity_tables(conn: Connection) -> None:
    """Replace current tables with the old consolidated global-key shape."""
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("DROP TRIGGER projection_inputs_no_update")
        conn.execute("DROP INDEX idx_projection_inputs_projection_sequence")
        conn.execute("DROP TABLE projection_inputs")
        conn.execute("DROP INDEX idx_structured_decisions_agent_kind_identity")
        conn.execute("DROP INDEX idx_structured_decisions_agent_response")
        conn.execute("DROP TABLE structured_decisions")
        conn.executescript(
            """
            CREATE TABLE structured_decisions (
                decision_request_id TEXT PRIMARY KEY,
                repository_id       TEXT NOT NULL,
                agent_id            TEXT NOT NULL,
                decision_kind       TEXT NOT NULL CHECK (decision_kind IN ('question', 'permission')),
                request_identity    TEXT NOT NULL,
                request_json        TEXT NOT NULL,
                response_json       TEXT,
                decided_by          TEXT,
                created_at          TEXT NOT NULL,
                responded_at        TEXT,
                CHECK ((response_json IS NULL) = (decided_by IS NULL)),
                CHECK ((response_json IS NULL) = (responded_at IS NULL))
            );
            CREATE TABLE projection_inputs (
                sequence        INTEGER PRIMARY KEY AUTOINCREMENT,
                input_id        TEXT NOT NULL UNIQUE,
                repository_id   TEXT NOT NULL,
                source_fact_id  TEXT REFERENCES retained_facts(fact_id) ON DELETE RESTRICT,
                projection      TEXT NOT NULL,
                subject_key     TEXT NOT NULL,
                generation      INTEGER NOT NULL CHECK (generation >= 0),
                created_at      TEXT NOT NULL,
                UNIQUE (source_fact_id, projection, subject_key, generation)
            );
            """
        )
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
