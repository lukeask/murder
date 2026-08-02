"""Service-side snapshot assembly for TUI and future service clients."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from murder.app.protocol.read_models import (
    ConversationsSnapshot,
    HistorySnapshot,
    NoteDisplaySnapshot,
    NotesSnapshot,
    PlanDisplaySnapshot,
    PlansSnapshot,
    ReportDisplaySnapshot,
    ReportsSnapshot,
    ScheduleSnapshot,
    TicketDetailSnapshot,
)
from murder.app.service.read_models._common import (
    FAILED_STALE_AFTER,
    STALE_AFTER_HOURS,
    GenerationKeys,
)
from murder.app.service.read_models.harness import HarnessReadModel
from murder.app.service.read_models.history import HistoryReadModel
from murder.app.service.read_models.runtime import RuntimeReadModel
from murder.app.service.read_models.transit import TransitReadModel
from murder.app.service.read_models.work import WorkReadModel
from murder.state.persistence.connection import RepoDb
from murder.state.storage.git_transit import TransitSnapshot


class ServiceReadModel:
    """Build immutable service snapshots from the SQLite persistence layer.

    Thin facade over per-domain builders (see ``read_models/``). Each public
    method delegates to its builder. A shared ``GenerationKeys`` provider keeps
    the invalidation generations in sync across all builders and the facade.

    Responsibility: own NO SQL. This class is a delegating face. Every query,
    schema-compat guard, and DTO mapping lives in a builder.

    Adding a snapshot/display — DO NOT add inline SQL here. The "one read model
    for everything" shape is what made this a god (671 lines before it was slain
    to a facade). Instead:
      • Put the builder method on the matching domain class — work / runtime /
        history / transit / harness (e.g. a new plan or ticket read goes on
        ``WorkReadModel`` in ``read_models/work.py``). Use
        ``self.keys.current_key(...)`` for the invalidation key and the shared
        helpers in ``read_models/_common.py``.
      • Add a one-line delegate here mirroring the others.
      • A genuinely new domain → a new ``read_models/<domain>.py`` builder,
      constructed in ``__init__`` with ``(self.db, self.repo_root, self._keys)``.
    Ousterhout: builders are deep modules (SQL + guards + mapping hidden behind a
    ``get_X_snapshot()`` call). The facade stays a thin, uniform interface.
    """

    def __init__(self, db: RepoDb, repo_root: Path) -> None:
        self.repo_root = Path(repo_root)
        self._repository_id = db.repository_id
        database_row = db.conn.execute("PRAGMA database_list").fetchone()
        database_file = "" if database_row is None else str(database_row[2] or "")
        if not database_file:
            raise RuntimeError("ServiceReadModel requires a file-backed database")
        self._database_path = Path(database_file)
        self._keys = GenerationKeys()

    @contextmanager
    def _read_db(self) -> Iterator[RepoDb]:
        """Open a connection owned by one read-model call.

        State handlers run these synchronous methods through ``asyncio.to_thread``.
        The runtime's long-lived connection therefore must not cross this boundary:
        it is concurrently used by the event-loop service and cannot safely be
        shared by arbitrary worker threads.
        """
        database_uri = f"{self._database_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(
            database_uri,
            uri=True,
            isolation_level=None,
            timeout=5.0,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield RepoDb(conn=conn, repository_id=self._repository_id)
        finally:
            conn.close()

    def get_plans_snapshot(self) -> PlansSnapshot:
        with self._read_db() as db:
            return WorkReadModel(db, self.repo_root, self._keys).get_plans_snapshot()

    def get_notes_snapshot(self) -> NotesSnapshot:
        with self._read_db() as db:
            return WorkReadModel(db, self.repo_root, self._keys).get_notes_snapshot()

    def get_reports_snapshot(self) -> ReportsSnapshot:
        with self._read_db() as db:
            return WorkReadModel(db, self.repo_root, self._keys).get_reports_snapshot()

    def get_ticket_detail(self, ticket_id: str) -> TicketDetailSnapshot:
        with self._read_db() as db:
            return WorkReadModel(db, self.repo_root, self._keys).get_ticket_detail(ticket_id)

    def get_plan_display(self, name: str) -> PlanDisplaySnapshot | None:
        with self._read_db() as db:
            return WorkReadModel(db, self.repo_root, self._keys).get_plan_display(name)

    def get_note_display(self, name: str) -> NoteDisplaySnapshot | None:
        with self._read_db() as db:
            return WorkReadModel(db, self.repo_root, self._keys).get_note_display(name)

    def get_report_display(self, name: str) -> ReportDisplaySnapshot | None:
        with self._read_db() as db:
            return WorkReadModel(db, self.repo_root, self._keys).get_report_display(name)

    def get_conversations_snapshot(self) -> ConversationsSnapshot:
        with self._read_db() as db:
            return RuntimeReadModel(db, self.repo_root, self._keys).get_conversations_snapshot()

    def get_schedule_snapshot(self) -> ScheduleSnapshot:
        with self._read_db() as db:
            return RuntimeReadModel(db, self.repo_root, self._keys).get_schedule_snapshot()

    def get_history_snapshot(self) -> HistorySnapshot:
        with self._read_db() as db:
            return HistoryReadModel(db, self.repo_root, self._keys).get_history_snapshot()

    def get_transit_snapshot(self) -> TransitSnapshot:
        with self._read_db() as db:
            return TransitReadModel(db, self.repo_root, self._keys).get_transit_snapshot()

    def get_harness_models_snapshot(self) -> dict[str, object]:
        with self._read_db() as db:
            return HarnessReadModel(db, self.repo_root, self._keys).get_harness_models_snapshot()

    def invalidate(self, key: str) -> None:
        self._keys.invalidate(key)

    def current_key(self, scope: str) -> str:
        return self._keys.current_key(scope)


__all__ = ["FAILED_STALE_AFTER", "STALE_AFTER_HOURS", "ServiceReadModel"]
