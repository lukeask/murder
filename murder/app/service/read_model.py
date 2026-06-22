"""Service-side snapshot assembly for TUI and future service clients."""

from __future__ import annotations

from pathlib import Path

from murder.app.service.client_api import (
    ConversationsSnapshot,
    CrowSnapshot,
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
from murder.state.storage.git_transit import TransitSnapshot


class ServiceReadModel:
    """Build immutable service snapshots from the SQLite persistence layer.

    Thin facade over per-domain builders (see ``read_models/``). Each public
    method delegates to its builder; a shared ``GenerationKeys`` provider keeps
    the invalidation generations in sync across all builders and the facade.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._keys = GenerationKeys()
        self._work = WorkReadModel(self.db_path, self._keys)
        self._runtime = RuntimeReadModel(self.db_path, self._keys)
        self._history = HistoryReadModel(self.db_path, self._keys)
        self._transit = TransitReadModel(self.db_path, self._keys)
        self._harness = HarnessReadModel(self.db_path, self._keys)

    def get_plans_snapshot(self) -> PlansSnapshot:
        return self._work.get_plans_snapshot()

    def get_notes_snapshot(self) -> NotesSnapshot:
        return self._work.get_notes_snapshot()

    def get_reports_snapshot(self) -> ReportsSnapshot:
        return self._work.get_reports_snapshot()

    def get_ticket_detail(self, ticket_id: str) -> TicketDetailSnapshot:
        return self._work.get_ticket_detail(ticket_id)

    def get_plan_display(self, name: str) -> PlanDisplaySnapshot | None:
        return self._work.get_plan_display(name)

    def get_note_display(self, name: str) -> NoteDisplaySnapshot | None:
        return self._work.get_note_display(name)

    def get_report_display(self, name: str) -> ReportDisplaySnapshot | None:
        return self._work.get_report_display(name)

    def get_crow_snapshot(self) -> CrowSnapshot:
        return self._runtime.get_crow_snapshot()

    def get_conversations_snapshot(self) -> ConversationsSnapshot:
        return self._runtime.get_conversations_snapshot()

    def get_schedule_snapshot(self) -> ScheduleSnapshot:
        return self._runtime.get_schedule_snapshot()

    def get_history_snapshot(self) -> HistorySnapshot:
        return self._history.get_history_snapshot()

    def get_transit_snapshot(self) -> TransitSnapshot:
        return self._transit.get_transit_snapshot()

    def get_harness_models_snapshot(self) -> dict[str, object]:
        return self._harness.get_harness_models_snapshot()

    def invalidate(self, key: str) -> None:
        self._keys.invalidate(key)

    def current_key(self, scope: str) -> str:
        return self._keys.current_key(scope)


__all__ = ["FAILED_STALE_AFTER", "STALE_AFTER_HOURS", "ServiceReadModel"]
