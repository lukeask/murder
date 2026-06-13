"""F1 — key-only event uniformity (backbone: protocol + agent choke point).

Covers:
- protocol version bump, ``Entity.REPORT`` enum member, reserved optional
  ``payload`` field on ``StateSnapshotEvent``;
- an agent mutation through the ``Runtime.sync_agent`` choke point emits exactly
  one key-only ``state.snapshot{entity=agent, key=<agent_id>}``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from murder.app.service.runtime import Runtime
from murder.bus import Bus
from murder.bus.protocol import PROTOCOL_VERSION, Entity, Role, StateSnapshotEvent
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.state.persistence.schema import get_db, init_db


# === protocol ===============================================================


def test_protocol_version_is_three() -> None:
    # F6 bumped PROTOCOL_VERSION 2→3 (TmuxFrameEvent); history view bumped 3→4
    # (Entity.HISTORY); transit view bumped 4→5 (Entity.TRANSIT).
    assert PROTOCOL_VERSION == 5


def test_entity_report_member_exists() -> None:
    assert Entity.REPORT == "report"
    assert "report" in {e.value for e in Entity}


def test_snapshot_payload_is_optional_and_defaults_none() -> None:
    ev = StateSnapshotEvent(run_id="r", entity=Entity.AGENT, key="a")
    assert ev.payload is None
    # And it accepts an inline payload without changing the key-only default.
    ev2 = StateSnapshotEvent(run_id="r", entity=Entity.AGENT, key="a", payload={"x": 1})
    assert ev2.payload == {"x": 1}


# === agent choke point ======================================================


@dataclass
class _FakeAgent:
    id: str
    role: Role = Role.CROW
    ticket_id: str | None = None
    session: str | None = "sess-1"
    status: object = field(default=None)

    def __post_init__(self) -> None:
        # ``sync_agent`` reads ``agent.role.value`` and ``agent.status.value``.
        from murder.bus.protocol import AgentStatus

        if self.status is None:
            self.status = AgentStatus.RUNNING


def _config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


@pytest.mark.asyncio
async def test_sync_agent_emits_exactly_one_key_only_agent_snapshot(
    repo_root: Path,
) -> None:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    rt = Runtime(_config(), repo_root)
    rt.db = conn
    rt.run_id = "run-test"
    rt.bus = Bus(rt.run_id, conn)

    captured: list[object] = []
    rt.bus.subscribe(lambda ev: _record(captured, ev))

    rt.sync_agent(_FakeAgent(id="crow-42"))

    # The publish is scheduled fire-and-forget onto the running loop; drain it
    # explicitly (conftest patches asyncio.sleep to noop, so sleeping won't flush).
    import asyncio

    assert rt._emit_tasks, "sync_agent should schedule a publish task"
    await asyncio.gather(*list(rt._emit_tasks))

    snapshots = [e for e in captured if isinstance(e, StateSnapshotEvent)]
    assert len(snapshots) == 1
    ev = snapshots[0]
    assert ev.entity == Entity.AGENT
    assert ev.key == "crow-42"
    assert ev.payload is None  # key-only by default


async def _record(sink: list[object], ev: object) -> None:
    sink.append(ev)
