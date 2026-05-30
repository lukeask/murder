from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from murder.bus.protocol import ClientKind
from murder.config import Config, CrowHandlerConfig, HarnessRoleConfig, ProjectConfig
from murder.service.client_api import DispatchSnapshot, TicketSummary, dto_to_wire
from murder.tickets.status import TicketStatus
from murder.tui.client import TuiRuntimeClient


class BusSimulator:
    def __init__(self, replies: dict[str, list[dict[str, Any]]]) -> None:
        self._replies = {target: list(items) for target, items in replies.items()}
        self.calls: list[tuple[str, dict[str, object]]] = []

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
