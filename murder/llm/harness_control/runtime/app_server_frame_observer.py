"""Read-only app-server frame source for verified controller observation loops."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from murder.llm.harness_control.app_server.connection import AppServerConnection
from murder.llm.harness_control.app_server.state import (
    AppServerViewState,
    apply_notification,
    apply_server_request,
    to_snapshot_dict,
)
from murder.llm.harness_control.model.evidence import FrameId, HarnessId, TerminalFrame

# Dummy pane geometry: app-server frames are JSON, not terminal cells.
_DUMMY_WIDTH = 80
_DUMMY_HEIGHT = 24


class AppServerFrameObserver:
    """Drains connection queues into view state and emits JSON ``TerminalFrame``s.

    ``raw_text`` is a deterministic v1 snapshot (``sort_keys=True``) so idle
    frames hash-stable for evidence fingerprinting.
    """

    def __init__(
        self,
        connection: AppServerConnection,
        harness_id: HarnessId,
        *,
        pane_epoch: int = 0,
        capture_sequence: int = 0,
        view_state: AppServerViewState | None = None,
    ) -> None:
        if pane_epoch < 0 or capture_sequence < 0:
            raise ValueError("pane epoch and capture sequence cannot be negative")
        self._connection = connection
        self._harness_id = harness_id
        self._pane_epoch = pane_epoch
        self._capture_sequence = capture_sequence
        self._view_state = view_state if view_state is not None else AppServerViewState()

    @property
    def view_state(self) -> AppServerViewState:
        return self._view_state

    async def capture_frame(self) -> TerminalFrame:
        for notification in self._connection.drain_notifications():
            apply_notification(self._view_state, notification)
        for request in self._connection.drain_incoming_requests():
            apply_server_request(self._view_state, request)

        # Keep connection ids in sync for adapter.lower() (interrupt / turn/start).
        if self._view_state.thread_id is not None:
            self._connection.thread_id = self._view_state.thread_id
        self._connection.current_turn_id = self._view_state.turn_id

        # Prefer connection-desired model/effort when view state has none yet.
        if self._view_state.model_id is None and self._connection.desired_model:
            self._view_state.model_id = self._connection.desired_model
        if self._view_state.effort is None and self._connection.desired_effort:
            self._view_state.effort = self._connection.desired_effort

        snapshot = to_snapshot_dict(
            self._view_state,
            staged_composer_text=self._connection.staged_composer_text,
            thread_id=self._connection.thread_id,
        )
        raw_text = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        self._capture_sequence += 1
        return TerminalFrame(
            frame_id=FrameId(str(uuid4())),
            harness_id=self._harness_id,
            captured_at=datetime.now(timezone.utc),
            width=_DUMMY_WIDTH,
            height=_DUMMY_HEIGHT,
            raw_text=raw_text,
            ansi_preserved=False,
            pane_epoch=self._pane_epoch,
            capture_sequence=self._capture_sequence,
            viewport_text=None,
        )


__all__ = ["AppServerFrameObserver"]
