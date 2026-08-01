"""SQLite journal implementation for the verified controller runtime."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from murder.llm.harness_control.model.actions import EmissionBatchResult
from murder.llm.harness_control.model.evidence import EvidenceEnvelope, TerminalFrame
from murder.llm.harness_control.model.observations import ObservationDelta, ObservationSnapshot
from murder.llm.harness_control.model.operations import ActionRecord, DecisionRecord
from murder.state.persistence import harness_control as persistence
from murder.state.persistence.connection import RepoDb


class SqliteHarnessControlJournal:
    """Durable journal that keeps control records out of generic command retry."""

    def __init__(self, db: RepoDb, *, session_id: str | None = None) -> None:
        self._db = db
        self._connection = db.conn
        self._session_id = session_id

    async def record_frame(self, frame: TerminalFrame) -> None:
        persistence.persist_frame(self._db, frame, session_id=self._session_id)
        self._connection.commit()

    async def record_evidence(self, evidence: Sequence[EvidenceEnvelope]) -> None:
        for envelope in evidence:
            persistence.persist_evidence(self._db, envelope)
        self._connection.commit()

    async def record_snapshot(self, snapshot: ObservationSnapshot) -> None:
        persistence.persist_observation_snapshot(
            self._db, snapshot, session_id=self._session_id
        )
        self._connection.commit()

    async def record_delta(self, snapshot: ObservationSnapshot, delta: ObservationDelta) -> None:
        persistence.persist_observation_delta(
            self._db,
            harness_id=str(snapshot.harness_id),
            session_id=self._session_id,
            revision=snapshot.revision,
            captured_at=snapshot.captured_at,
            delta=delta,
        )
        self._connection.commit()

    async def record_operation(self, operation: object, snapshot: ObservationSnapshot) -> None:
        envelope = getattr(operation, "envelope", None)
        if envelope is None:
            raise TypeError("verified operations must expose an OperationEnvelope as envelope")
        persistence.persist_operation(
            self._db,
            envelope,
            harness_id=str(snapshot.harness_id),
            session_id=self._session_id,
            request=getattr(operation, "request", None),
            operation_state=operation,
        )
        self._connection.commit()

    async def record_decision(self, decision: DecisionRecord) -> None:
        persistence.persist_decision_record(self._db, decision)
        self._connection.commit()

    async def prepare_action(self, action: ActionRecord) -> None:
        # persist_action_record performs the action+effect inserts before this
        # method returns, which is the required crash boundary before tmux I/O.
        self._persist_atomically(
            lambda: persistence.persist_action_record(self._db, action),
            savepoint="prepare_action",
        )

    async def record_transition(
        self,
        operation: object,
        snapshot: ObservationSnapshot,
        decision: DecisionRecord,
        action: ActionRecord | None,
    ) -> None:
        """Atomically persist advanced state, decision, and prepared effects."""

        envelope = getattr(operation, "envelope", None)
        if envelope is None:
            raise TypeError("verified operations must expose an OperationEnvelope as envelope")
        def persist() -> None:
            persistence.persist_operation(
                self._db,
                envelope,
                harness_id=str(snapshot.harness_id),
                session_id=self._session_id,
                request=getattr(operation, "request", None),
                operation_state=operation,
            )
            persistence.persist_decision_record(self._db, decision)
            if action is not None:
                persistence.persist_action_record(self._db, action)

        self._persist_atomically(persist, savepoint="transition")

    async def record_emission(self, action: ActionRecord, result: EmissionBatchResult) -> None:
        persistence.record_effect_emissions(
            self._db,
            action_id=action.action_id,
            results=result.results,
            emitted_at=datetime.now(timezone.utc),
        )
        self._connection.commit()

    def _persist_atomically(self, persist: Callable[[], None], *, savepoint: str) -> None:
        """Make one verified persistence boundary all-or-nothing.

        The production connection uses SQLite autocommit.  A savepoint starts
        and commits a transaction in that mode, while also remaining safe if a
        caller later composes the journal inside an outer transaction.  Keeping
        the transaction here prevents the individual persistence helpers from
        leaking partial operation, decision, action, or effect rows.
        """

        name = f"harness_control_{savepoint}"
        self._connection.execute(f"SAVEPOINT {name}")
        try:
            persist()
        except BaseException:
            self._connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
            self._connection.execute(f"RELEASE SAVEPOINT {name}")
            raise
        self._connection.execute(f"RELEASE SAVEPOINT {name}")
