"""Read-only Claude Agent SDK frame source for verified controller observation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from murder.llm.harness_control.agent_sdk.connection import AgentSdkConnection
from murder.llm.harness_control.agent_sdk.state import (
    AgentSdkViewState,
    apply_event,
    apply_permission_request,
    to_snapshot_dict,
)
from murder.llm.harness_control.model.evidence import FrameId, HarnessId, TerminalFrame

_DUMMY_WIDTH = 80
_DUMMY_HEIGHT = 24


class AgentSdkFrameObserver:
    """Drains connection queues into view state and emits JSON ``TerminalFrame``s."""

    def __init__(
        self,
        connection: AgentSdkConnection,
        harness_id: HarnessId,
        *,
        pane_epoch: int = 0,
        capture_sequence: int = 0,
        view_state: AgentSdkViewState | None = None,
    ) -> None:
        if pane_epoch < 0 or capture_sequence < 0:
            raise ValueError("pane epoch and capture sequence cannot be negative")
        self._connection = connection
        self._harness_id = harness_id
        self._pane_epoch = pane_epoch
        self._capture_sequence = capture_sequence
        self._view_state = view_state if view_state is not None else AgentSdkViewState()

    @property
    def view_state(self) -> AgentSdkViewState:
        return self._view_state

    async def capture_frame(self) -> TerminalFrame:
        for event in self._connection.drain_messages():
            apply_event(self._view_state, event)
        for request in self._connection.drain_incoming_requests():
            apply_permission_request(self._view_state, request)

        if self._view_state.session_id is not None:
            self._connection.session_id = self._view_state.session_id
        if self._view_state.model_id is None and self._connection.desired_model:
            self._view_state.model_id = self._connection.desired_model
        if self._view_state.effort is None and self._connection.desired_effort:
            self._view_state.effort = self._connection.desired_effort
        if self._connection.prompt_in_flight and self._view_state.turn_status in {
            None,
            "idle",
            "completed",
        }:
            self._view_state.turn_status = "streaming"

        snapshot = to_snapshot_dict(
            self._view_state,
            staged_composer_text=self._connection.staged_composer_text,
            session_id=self._connection.session_id,
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


__all__ = ["AgentSdkFrameObserver"]
