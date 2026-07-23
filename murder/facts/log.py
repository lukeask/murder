"""Append-only SQLite fact log and transactional projection-input boundary."""

from __future__ import annotations

import json
import asyncio
import sqlite3
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from murder.facts.contracts import (
    AggregateRef,
    FactActor,
    FactCorrelation,
    ProjectionInputDraft,
    ProjectionInputRecord,
    RetainedFactDraft,
    RetainedFactRecord,
    fact_payload_storage,
)

FACT_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS retained_facts (
    sequence            INTEGER PRIMARY KEY AUTOINCREMENT,
    fact_id             TEXT NOT NULL UNIQUE,
    kind                TEXT NOT NULL,
    schema_version      INTEGER NOT NULL CHECK (schema_version >= 1),
    occurred_at         TEXT NOT NULL,
    recorded_at         TEXT NOT NULL,
    aggregate_kind      TEXT,
    aggregate_id        TEXT,
    aggregate_revision  INTEGER CHECK (
                            aggregate_revision IS NULL
                            OR aggregate_revision >= 0
                        ),
    actor_kind          TEXT NOT NULL,
    actor_id            TEXT NOT NULL,
    correlation_id      TEXT NOT NULL,
    causation_id        TEXT,
    trace_id            TEXT,
    payload_json        TEXT NOT NULL,
    CHECK ((aggregate_kind IS NULL) = (aggregate_id IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_retained_facts_kind_sequence
    ON retained_facts(kind, sequence);
CREATE INDEX IF NOT EXISTS idx_retained_facts_aggregate_sequence
    ON retained_facts(aggregate_kind, aggregate_id, sequence);
CREATE TRIGGER IF NOT EXISTS retained_facts_no_update
BEFORE UPDATE ON retained_facts
BEGIN
    SELECT RAISE(ABORT, 'retained facts are immutable');
END;
CREATE TABLE IF NOT EXISTS projection_inputs (
    sequence        INTEGER PRIMARY KEY AUTOINCREMENT,
    input_id        TEXT NOT NULL UNIQUE,
    source_fact_id  TEXT REFERENCES retained_facts(fact_id) ON DELETE RESTRICT,
    projection      TEXT NOT NULL,
    subject_key     TEXT NOT NULL,
    generation      INTEGER NOT NULL CHECK (generation >= 0),
    created_at      TEXT NOT NULL,
    UNIQUE (source_fact_id, projection, subject_key, generation)
);
CREATE INDEX IF NOT EXISTS idx_projection_inputs_projection_sequence
    ON projection_inputs(projection, sequence);
CREATE TRIGGER IF NOT EXISTS projection_inputs_no_update
BEFORE UPDATE ON projection_inputs
BEGIN
    SELECT RAISE(ABORT, 'projection inputs are immutable');
END;
"""


class FactLogError(RuntimeError):
    pass


class FactIdentityConflictError(FactLogError):
    """A fact id was reused for different immutable content."""


class ReplayGapError(FactLogError):
    """A retained cursor can no longer be replayed."""


class FactLog:
    """The durable, immutable fact stream.

    This deliberately owns only fact persistence, retention, replay, and
    tailing.  It is not an event bus and has no request routing surface.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        poll_interval_s: float = 0.05,
        retention_min_records: int = 20_000,
        retention_max_age_days: int = 7,
    ) -> None:
        self._connection = connection
        self._poll_interval_s = poll_interval_s
        self._retention_min_records = retention_min_records
        self._retention_max_age = timedelta(days=retention_max_age_days)
        ensure_fact_schema(connection)

    def append(
        self,
        draft: RetainedFactDraft,
        *,
        projection_inputs: Sequence[ProjectionInputDraft] = (),
        recorded_at: datetime | None = None,
    ) -> tuple[RetainedFactRecord, tuple[ProjectionInputRecord, ...]]:
        return append_fact(
            self._connection,
            draft,
            projection_inputs=projection_inputs,
            recorded_at=recorded_at,
        )

    def watermark(self) -> int:
        return _watermark(self._connection, "retained_facts")

    def is_cursor_retained(self, cursor: int) -> bool:
        return _cursor_retained(self._connection, "retained_facts", cursor)

    def replay(
        self,
        *,
        after_sequence: int,
        kinds: frozenset[str] = frozenset(),
        until_sequence: int | None = None,
    ) -> tuple[RetainedFactRecord, ...]:
        return replay_facts(
            self._connection,
            after_sequence=after_sequence,
            kinds=kinds,
            until_sequence=until_sequence,
            limit=100_000,
        )

    async def tail(
        self, *, after_sequence: int, kinds: frozenset[str] = frozenset()
    ) -> AsyncIterator[RetainedFactRecord]:
        cursor = after_sequence
        while True:
            if not self.is_cursor_retained(cursor):
                raise ReplayGapError(f"fact cursor {cursor} is outside retained history")
            records = self.replay(after_sequence=cursor, kinds=kinds)
            if records:
                for record in records:
                    cursor = record.sequence
                    yield record
            else:
                await asyncio.sleep(self._poll_interval_s)

    def prune(self, *, now: datetime | None = None) -> int:
        overage = _record_overage(self._connection, "retained_facts", self._retention_min_records)
        if overage <= 0:
            return 0
        cutoff = ((now or datetime.now(timezone.utc)) - self._retention_max_age).isoformat(
            timespec="seconds"
        )
        rows = self._connection.execute(
            """
            SELECT f.fact_id FROM retained_facts AS f
            WHERE f.recorded_at < ?
              AND NOT EXISTS (SELECT 1 FROM projection_inputs AS p WHERE p.source_fact_id = f.fact_id)
            ORDER BY f.sequence ASC LIMIT ?
            """,
            (cutoff, overage),
        ).fetchall()
        ids = [str(row["fact_id"]) for row in rows]
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        result = self._connection.execute(
            f"DELETE FROM retained_facts WHERE fact_id IN ({placeholders})", ids
        )
        return int(result.rowcount or 0)


class ProjectionInputLog:
    """Durable invalidation stream, separate from facts and orchestration."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        poll_interval_s: float = 0.05,
        retention_min_records: int = 20_000,
        retention_max_age_days: int = 7,
    ) -> None:
        self._connection = connection
        self._poll_interval_s = poll_interval_s
        self._retention_min_records = retention_min_records
        self._retention_max_age = timedelta(days=retention_max_age_days)
        ensure_fact_schema(connection)

    def append(self, draft: ProjectionInputDraft) -> ProjectionInputRecord:
        return append_projection_input(self._connection, draft)

    def watermark(self) -> int:
        return _watermark(self._connection, "projection_inputs")

    def is_cursor_retained(self, cursor: int) -> bool:
        return _cursor_retained(self._connection, "projection_inputs", cursor)

    def replay(
        self, *, after_sequence: int, projections: frozenset[str], until_sequence: int | None = None,
        limit: int = 1000,
    ) -> tuple[ProjectionInputRecord, ...]:
        return replay_projection_inputs(self._connection, projections=projections,
            after_sequence=after_sequence, until_sequence=until_sequence, limit=limit)

    async def tail(self, *, after_sequence: int, projections: frozenset[str]) -> AsyncIterator[ProjectionInputRecord]:
        cursor = after_sequence
        while True:
            if not self.is_cursor_retained(cursor):
                raise ReplayGapError(f"projection cursor {cursor} is outside retained history")
            records = self.replay(after_sequence=cursor, projections=projections)
            if records:
                cursor = records[-1].sequence
                latest = {(item.projection, item.subject_key): item for item in records}
                for item in sorted(latest.values(), key=lambda value: value.sequence):
                    yield item
            else:
                await asyncio.sleep(self._poll_interval_s)

    def prune(self, *, now: datetime | None = None) -> int:
        overage = _record_overage(self._connection, "projection_inputs", self._retention_min_records)
        if overage <= 0:
            return 0
        cutoff = ((now or datetime.now(timezone.utc)) - self._retention_max_age).isoformat(
            timespec="seconds"
        )
        result = self._connection.execute(
            "DELETE FROM projection_inputs WHERE sequence IN (SELECT sequence FROM projection_inputs WHERE created_at < ? ORDER BY sequence ASC LIMIT ?)",
            (cutoff, overage),
        )
        return int(result.rowcount or 0)


def append_fact(
    conn: sqlite3.Connection,
    draft: RetainedFactDraft,
    *,
    projection_inputs: Sequence[ProjectionInputDraft] = (),
    recorded_at: datetime | None = None,
) -> tuple[RetainedFactRecord, tuple[ProjectionInputRecord, ...]]:
    """Append one fact and all projection inputs atomically.

    ``fact_id`` is an idempotency identity. Re-appending identical content
    converges on the original row; reusing it for different content fails.
    Projection inputs are unique by their source fact and key tuple, so retrying
    the whole operation cannot create duplicate invalidations.
    """

    timestamp = _aware(recorded_at)
    with _savepoint(conn):
        existing = _fact_row(conn, draft.fact_id)
        if existing is None:
            aggregate = draft.aggregate
            conn.execute(
                """
                INSERT INTO retained_facts(
                    fact_id, kind, schema_version, occurred_at, recorded_at,
                    aggregate_kind, aggregate_id, aggregate_revision,
                    actor_kind, actor_id, correlation_id, causation_id, trace_id,
                    payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(draft.fact_id),
                    draft.kind,
                    draft.schema_version,
                    _datetime_text(draft.occurred_at),
                    _datetime_text(timestamp),
                    aggregate.kind if aggregate is not None else None,
                    str(aggregate.id) if aggregate is not None else None,
                    aggregate.revision if aggregate is not None else None,
                    draft.actor.kind,
                    draft.actor.id,
                    str(draft.correlation.correlation_id),
                    _optional_uuid(draft.correlation.causation_id),
                    _optional_uuid(draft.correlation.trace_id),
                    _json(fact_payload_storage(draft.payload)),
                ),
            )
            existing = _fact_row(conn, draft.fact_id)
            assert existing is not None
        elif not _same_fact(existing, draft):
            raise FactIdentityConflictError(
                f"fact id {draft.fact_id} already identifies different content"
            )

        for item in projection_inputs:
            conn.execute(
                """
                INSERT INTO projection_inputs(
                    input_id, source_fact_id, projection, subject_key, generation, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                (
                    str(item.input_id),
                    str(draft.fact_id),
                    item.projection,
                    item.subject_key,
                    item.generation,
                    _datetime_text(timestamp),
                ),
            )

        fact = _record_from_row(existing)
        inputs = tuple(
            _projection_record(row)
            for row in conn.execute(
                """
                SELECT sequence, input_id, source_fact_id, projection, subject_key,
                       generation, created_at
                FROM projection_inputs
                WHERE source_fact_id = ?
                ORDER BY sequence
                """,
                (str(draft.fact_id),),
            ).fetchall()
        )
    return fact, inputs


def ensure_fact_schema(conn: sqlite3.Connection) -> None:
    """Create the append-only boundary without committing an outer transaction."""

    # Named rows remain tuple-compatible for feature DAOs sharing the handle.
    conn.row_factory = sqlite3.Row
    for statement in _schema_statements(FACT_SCHEMA_SQL):
        conn.execute(statement)


def append_projection_input(
    conn: sqlite3.Connection,
    draft: ProjectionInputDraft,
    *,
    created_at: datetime | None = None,
) -> ProjectionInputRecord:
    """Append a durable invalidation without manufacturing a public fact."""

    timestamp = _aware(created_at)
    with _savepoint(conn):
        conn.execute(
            """
            INSERT INTO projection_inputs(
                input_id, source_fact_id, projection, subject_key, generation, created_at
            ) VALUES (?, NULL, ?, ?, ?, ?)
            ON CONFLICT(input_id) DO NOTHING
            """,
            (
                str(draft.input_id),
                draft.projection,
                draft.subject_key,
                draft.generation,
                _datetime_text(timestamp),
            ),
        )
        row = conn.execute(
            """
            SELECT sequence, input_id, source_fact_id, projection, subject_key,
                   generation, created_at
              FROM projection_inputs
             WHERE input_id = ?
            """,
            (str(draft.input_id),),
        ).fetchone()
        assert row is not None
        record = _projection_record(row)
        if (
            record.projection != draft.projection
            or record.subject_key != draft.subject_key
            or record.generation != draft.generation
        ):
            raise FactIdentityConflictError(
                f"projection input id {draft.input_id} identifies different content"
            )
    return record


def get_fact(conn: sqlite3.Connection, fact_id: UUID) -> RetainedFactRecord | None:
    row = _fact_row(conn, fact_id)
    return None if row is None else _record_from_row(row)


def replay_facts(
    conn: sqlite3.Connection,
    *,
    after_sequence: int = 0,
    kind: str | None = None,
    kinds: frozenset[str] = frozenset(),
    until_sequence: int | None = None,
    limit: int = 1000,
) -> tuple[RetainedFactRecord, ...]:
    if after_sequence < 0:
        raise ValueError("after_sequence must not be negative")
    if limit < 1:
        raise ValueError("limit must be positive")
    if kind is not None and kinds:
        raise ValueError("kind and kinds are mutually exclusive")
    predicates: list[str] = []
    parameters: list[object] = [after_sequence]
    if kind is not None:
        predicates.append("kind = ?")
        parameters.append(kind)
    elif kinds:
        placeholders = ",".join("?" for _ in kinds)
        predicates.append(f"kind IN ({placeholders})")
        parameters.extend(sorted(kinds))
    if until_sequence is not None:
        if until_sequence < after_sequence:
            return ()
        predicates.append("sequence <= ?")
        parameters.append(until_sequence)
    predicate = "".join(f" AND {item}" for item in predicates)
    parameters.append(limit)
    rows = conn.execute(
        f"""
        SELECT *
        FROM retained_facts
        WHERE sequence > ?{predicate}
        ORDER BY sequence
        LIMIT ?
        """,
        tuple(parameters),
    ).fetchall()
    return tuple(_record_from_row(row) for row in rows)


def replay_projection_inputs(
    conn: sqlite3.Connection,
    *,
    projection: str | None = None,
    projections: frozenset[str] = frozenset(),
    after_sequence: int = 0,
    until_sequence: int | None = None,
    limit: int = 1000,
) -> tuple[ProjectionInputRecord, ...]:
    if projection is not None and not projection:
        raise ValueError("projection must not be empty")
    if projection is not None and projections:
        raise ValueError("projection and projections are mutually exclusive")
    if after_sequence < 0:
        raise ValueError("after_sequence must not be negative")
    if limit < 1:
        raise ValueError("limit must be positive")
    predicates = ["sequence > ?"]
    parameters: list[object] = [after_sequence]
    if projection is not None:
        predicates.append("projection = ?")
        parameters.append(projection)
    elif projections:
        placeholders = ",".join("?" for _ in projections)
        predicates.append(f"projection IN ({placeholders})")
        parameters.extend(sorted(projections))
    if until_sequence is not None:
        if until_sequence < after_sequence:
            return ()
        predicates.append("sequence <= ?")
        parameters.append(until_sequence)
    parameters.append(limit)
    rows = conn.execute(
        f"""
        SELECT sequence, input_id, source_fact_id, projection, subject_key,
               generation, created_at
        FROM projection_inputs
        WHERE {" AND ".join(predicates)}
        ORDER BY sequence
        LIMIT ?
        """,
        tuple(parameters),
    ).fetchall()
    return tuple(_projection_record(row) for row in rows)


def _fact_row(conn: sqlite3.Connection, fact_id: UUID) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM retained_facts WHERE fact_id = ?",
        (str(fact_id),),
    ).fetchone()


def _same_fact(row: sqlite3.Row, draft: RetainedFactDraft) -> bool:
    aggregate = draft.aggregate
    return (
        str(row["kind"]) == draft.kind
        and int(row["schema_version"]) == draft.schema_version
        and str(row["occurred_at"]) == _datetime_text(draft.occurred_at)
        and row["aggregate_kind"] == (aggregate.kind if aggregate is not None else None)
        and row["aggregate_id"] == (str(aggregate.id) if aggregate is not None else None)
        and row["aggregate_revision"] == (aggregate.revision if aggregate is not None else None)
        and str(row["actor_kind"]) == draft.actor.kind
        and str(row["actor_id"]) == draft.actor.id
        and str(row["correlation_id"]) == str(draft.correlation.correlation_id)
        and row["causation_id"] == _optional_uuid(draft.correlation.causation_id)
        and row["trace_id"] == _optional_uuid(draft.correlation.trace_id)
        and str(row["payload_json"]) == _json(fact_payload_storage(draft.payload))
    )


def _record_from_row(row: sqlite3.Row) -> RetainedFactRecord:
    aggregate = (
        None
        if row["aggregate_kind"] is None
        else AggregateRef(
            kind=str(row["aggregate_kind"]),
            id=UUID(str(row["aggregate_id"])),
            revision=(
                None if row["aggregate_revision"] is None else int(row["aggregate_revision"])
            ),
        )
    )
    return RetainedFactRecord(
        sequence=int(row["sequence"]),
        fact_id=UUID(str(row["fact_id"])),
        kind=str(row["kind"]),
        schema_version=int(row["schema_version"]),
        occurred_at=_parse_datetime(row["occurred_at"]),
        recorded_at=_parse_datetime(row["recorded_at"]),
        aggregate=aggregate,
        actor=FactActor(kind=str(row["actor_kind"]), id=str(row["actor_id"])),
        correlation=FactCorrelation(
            correlation_id=UUID(str(row["correlation_id"])),
            causation_id=_parse_optional_uuid(row["causation_id"]),
            trace_id=_parse_optional_uuid(row["trace_id"]),
        ),
        payload=json.loads(str(row["payload_json"])),
    )


def _projection_record(row: sqlite3.Row) -> ProjectionInputRecord:
    return ProjectionInputRecord(
        sequence=int(row["sequence"]),
        input_id=UUID(str(row["input_id"])),
        source_fact_id=(
            None
            if row["source_fact_id"] is None
            else UUID(str(row["source_fact_id"]))
        ),
        projection=str(row["projection"]),
        subject_key=str(row["subject_key"]),
        generation=int(row["generation"]),
        created_at=_parse_datetime(row["created_at"]),
    )


@contextmanager
def _savepoint(conn: sqlite3.Connection) -> Iterator[None]:
    name = f"append_fact_{uuid4().hex}"
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        conn.execute(f"ROLLBACK TO {name}")
        conn.execute(f"RELEASE {name}")
        raise
    else:
        conn.execute(f"RELEASE {name}")


def _json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _aware(value: datetime | None) -> datetime:
    candidate = value or datetime.now(timezone.utc)
    if candidate.tzinfo is None or candidate.utcoffset() is None:
        raise ValueError("fact timestamps must be timezone-aware")
    return candidate.astimezone(timezone.utc)


def _datetime_text(value: datetime) -> str:
    return _aware(value).isoformat()


def _parse_datetime(value: object) -> datetime:
    return datetime.fromisoformat(str(value))


def _optional_uuid(value: UUID | None) -> str | None:
    return None if value is None else str(value)


def _parse_optional_uuid(value: object) -> UUID | None:
    return None if value is None else UUID(str(value))


def _schema_statements(script: str) -> Iterator[str]:
    """Split this fixed DDL while preserving trigger bodies."""

    statement: list[str] = []
    in_trigger = False
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("CREATE TRIGGER"):
            in_trigger = True
        statement.append(line)
        if (in_trigger and stripped == "END;") or (
            not in_trigger and stripped.endswith(";")
        ):
            yield "\n".join(statement)
            statement = []
            in_trigger = False
    if statement:
        yield "\n".join(statement)


def _watermark(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(f"SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM {table}").fetchone()
    return int(row["max_sequence"] if row is not None else 0)


def _cursor_retained(connection: sqlite3.Connection, table: str, cursor: int) -> bool:
    if cursor < 0 or cursor > _watermark(connection, table):
        return False
    row = connection.execute(f"SELECT MIN(sequence) AS min_sequence FROM {table}").fetchone()
    if row is None or row["min_sequence"] is None:
        return cursor == 0
    return cursor >= int(row["min_sequence"]) - 1


def _record_overage(connection: sqlite3.Connection, table: str, minimum: int) -> int:
    row = connection.execute(f"SELECT COUNT(*) AS n_records FROM {table}").fetchone()
    return int(row["n_records"] if row is not None else 0) - minimum


__all__ = [
    "FACT_SCHEMA_SQL",
    "FactIdentityConflictError",
    "FactLogError",
    "FactLog",
    "ProjectionInputLog",
    "ReplayGapError",
    "append_fact",
    "append_projection_input",
    "ensure_fact_schema",
    "get_fact",
    "replay_facts",
    "replay_projection_inputs",
]
