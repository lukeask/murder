from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from murder.app.service.client_api import (
    ConversationBlockSummary,
    ConversationsSnapshot,
    ConversationSummary,
    DispatchSnapshot,
    TicketSummary,
    dto_to_wire,
)
from murder.app.tui.client import TuiRuntimeClient
from murder.app.tui.conversations import ConversationProjection
from murder.bus.protocol import (
    BUS_EVENT_ADAPTER,
    ClientKind,
    ConversationBlockEvent,
    EventFilter,
)
from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig
from murder.work.tickets.status import TicketStatus


class BusSimulator:
    def __init__(self, replies: dict[str, list[dict[str, Any]]]) -> None:
        self._replies = {target: list(items) for target, items in replies.items()}
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.subscriptions: list[EventFilter] = []
        self.events: list[Any] = []

    async def request(
        self,
        target: str,
        body: dict[str, object],
        *,
        timeout_s: float,  # noqa: ARG002
    ) -> dict[str, Any]:
        self.calls.append((target, dict(body)))
        replies = self._replies[target]
        if not replies:
            raise AssertionError(f"unexpected extra request to {target}")
        return replies.pop(0)

    async def subscribe(self, event_filter: EventFilter):
        self.subscriptions.append(event_filter)
        for event in self.events:
            if event_filter.matches(event):
                yield event


def _config(repo_root: Path) -> Config:
    role = HarnessRoleConfig(harness="codex")
    return Config(
        project=ProjectConfig(name="test", repo_path=repo_root),
        collaborator=role,
        crow_handler=CrowHandlerConfig(model="test-crow-handler"),
        default_crow=role,
    )


def test_tui_dispatch_snapshot_comes_from_bus_without_opening_db(repo_root) -> None:
    client = TuiRuntimeClient(
        repo_root,
        repo_root / "bus.sock",
        _config(repo_root),
        client_kind=ClientKind.TUI,
    )
    snapshot = DispatchSnapshot(
        tickets=(
            TicketSummary(
                id="t001",
                title="Bus only",
                status=TicketStatus.PLANNED,
                wave=1,
                harness="codex",
                model=None,
            ),
        ),
        as_of=datetime(2026, 1, 1, 12, 0, 0),
        invalidation_key="dispatch-1",
    )
    bus = BusSimulator(
        {"state.dispatch_snapshot": [{"ok": True, "value": dto_to_wire(snapshot)}]}
    )
    client.bus = bus  # type: ignore[assignment]

    got = asyncio.run(client.get_dispatch_snapshot())

    assert got == snapshot
    assert bus.calls == [("state.dispatch_snapshot", {})]
    assert not (repo_root / ".murder" / "murder.db").exists()


def test_tui_conversations_snapshot_bootstraps_from_bus(repo_root) -> None:
    client = TuiRuntimeClient(repo_root, repo_root / "bus.sock", _config(repo_root))
    snapshot = ConversationsSnapshot(
        conversations=(
            ConversationSummary(
                conversation_id="crow-t001",
                agent_id="crow-t001",
                harness="codex",
                model="gpt-5.1",
                harness_session_id=None,
                live_state="working",
                condensed=None,
                status="in_progress",
                blocks=(
                    ConversationBlockSummary(
                        id=1,
                        conversation_id="crow-t001",
                        ordinal=0,
                        kind="user",
                        payload={"type": "user", "text": "hello"},
                        sealed=True,
                        service_received_at="2026-06-06T12:00:00",
                    ),
                ),
            ),
        ),
        as_of=datetime(2026, 6, 6, 12, 0, 0),
        invalidation_key="conversations-1",
    )
    bus = BusSimulator(
        {"state.conversations_snapshot": [{"ok": True, "value": dto_to_wire(snapshot)}]}
    )
    client.bus = bus  # type: ignore[assignment]

    got = asyncio.run(client.get_conversations_snapshot())

    assert got == snapshot
    assert bus.calls == [("state.conversations_snapshot", {})]


def test_conversation_projection_resolves_collaborator_agent_id_from_snapshot() -> None:
    snapshot = ConversationsSnapshot(
        conversations=(
            ConversationSummary(
                conversation_id="collaborator-0",
                agent_id="collaborator-0",
                harness="codex",
                model=None,
                harness_session_id=None,
                live_state="awaiting_input",
                condensed=None,
                status="in_progress",
                blocks=(),
            ),
        ),
        as_of=datetime(2026, 6, 6, 12, 0, 0),
        invalidation_key="conversations-1",
    )
    projection = ConversationProjection()

    projection.bootstrap(snapshot)

    assert projection.conversation_id_for_agent_prefix("collaborator") == "collaborator-0"


def test_conversation_projection_resolves_agent_id_from_live_event() -> None:
    projection = ConversationProjection()
    event = ConversationBlockEvent(
        run_id="run-1",
        agent_id="collaborator-0",
        conversation_id="collaborator-0",
        action="block-appended",
        block={
            "id": 1,
            "conversation_id": "collaborator-0",
            "ordinal": 0,
            "kind": "user",
            "payload": {"type": "user", "text": "hello"},
            "sealed": True,
            "service_received_at": "2026-06-06T12:00:00",
        },
    )

    projection.apply_event(event)

    assert projection.conversation_id_for_agent("collaborator-0") == "collaborator-0"
    assert projection.conversation_id_for_agent_prefix("collaborator") == "collaborator-0"
    doc = projection.doc_for("collaborator-0")
    assert doc is not None
    assert doc["segments"] == [{"type": "user", "text": "hello"}]


def test_tui_subscribes_to_conversation_block_events(repo_root) -> None:
    client = TuiRuntimeClient(repo_root, repo_root / "bus.sock", _config(repo_root))
    event = ConversationBlockEvent(
        run_id="run-1",
        agent_id="crow-t001",
        conversation_id="crow-t001",
        action="block-appended",
        block={
            "id": 1,
            "conversation_id": "crow-t001",
            "ordinal": 0,
            "kind": "user",
            "payload": {"type": "user", "text": "hello"},
            "sealed": True,
            "service_received_at": "2026-06-06T12:00:00",
        },
    )
    bus = BusSimulator({})
    bus.events = [event]
    client.bus = bus  # type: ignore[assignment]

    async def collect_one() -> Any:
        async for item in client.subscribe_conversation_blocks():
            return item
        raise AssertionError("no conversation block event yielded")

    got = asyncio.run(collect_one())

    assert got == event
    assert bus.subscriptions == [EventFilter(type="conversation.block")]


def test_conversation_block_event_round_trips_through_protocol_adapter() -> None:
    event = ConversationBlockEvent(
        run_id="run-1",
        agent_id="planner-plan-a",
        role=None,
        ticket_id=None,
        conversation_id="planner-plan-a",
        action="block-updated",
        block={
            "id": 7,
            "conversation_id": "planner-plan-a",
            "ordinal": 2,
            "kind": "assistant_intermediate",
            "payload": {"type": "assistant", "phase": "intermediate", "text": "working"},
            "sealed": False,
            "service_received_at": "2026-06-06T12:01:00",
        },
    )

    payload = event.model_dump(mode="json")
    parsed = BUS_EVENT_ADAPTER.validate_python(payload)

    assert isinstance(parsed, ConversationBlockEvent)
    assert parsed.type == "conversation.block"
    assert parsed.action == "block-updated"
    assert parsed.block["payload"] == {
        "type": "assistant",
        "phase": "intermediate",
        "text": "working",
    }


def test_tui_ack_escalation_submits_state_command_over_bus(repo_root) -> None:
    client = TuiRuntimeClient(repo_root, repo_root / "bus.sock", _config(repo_root))
    bus = BusSimulator(
        {
            "command.submit": [{"ok": True, "command_id": "cmd-1"}],
            "command.status": [{"ok": True, "status": "done", "result_json": "{}"}],
        }
    )
    client.bus = bus  # type: ignore[assignment]

    asyncio.run(client.ack_escalation(42))

    assert bus.calls == [
        (
            "command.submit",
            {
                "target_worker": "state",
                "kind": "state.escalation.ack",
                "payload": {"escalation_id": 42},
                "agent_id": "tui",
                "correlation_id": bus.calls[0][1]["correlation_id"],
                "idempotency_key": bus.calls[0][1]["idempotency_key"],
            },
        ),
        ("command.status", {"command_id": "cmd-1"}),
    ]
