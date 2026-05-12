from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from murder.agents.base import AgentStatus
from murder.agents.crow_handler import CrowHandlerAgent
from murder.agents.sentinel import SentinelAgent
from murder.config import CrowHandlerConfig, SentinelConfig
from murder.harnesses.base import HarnessAdapter


class _FakeHarness(HarnessAdapter):
    kind = "fake"
    crow_system_prompt = ""

    def startup_cmd(self, cwd: Path) -> list[str]:
        return ["fake"]

    def is_ready(self, pane_text: str) -> bool:
        return True

    def is_idle(self, pane_text: str) -> bool:
        return False

    def is_busy(self, pane_text: str) -> bool:
        return False

    def extract_last_message(self, pane_text: str) -> str | None:
        return pane_text or None

    def format_nudge(self, msg: str) -> str:
        return msg


class _Bus:
    def __init__(self) -> None:
        self.events: list[object] = []

    async def publish(self, event: object) -> None:
        self.events.append(event)


class _Runtime:
    def __init__(self) -> None:
        self.run_id = "run-1"
        self.bus = _Bus()
        self.db = None
        self.synced: list[AgentStatus] = []

    def sync_agent(self, agent) -> None:
        self.synced.append(agent.status)


def _crow_handler(runtime: _Runtime) -> CrowHandlerAgent:
    return CrowHandlerAgent(
        agent_id="crow_handler-t001",
        ticket_id="t001",
        session="crow_handler-session",
        crow_session="crow-session",
        harness=_FakeHarness(),
        config=CrowHandlerConfig(model="crow_handler-model", poll_interval_s=0.01),
        repo_root=Path("/repo"),
        runtime=runtime,  # type: ignore[arg-type]
        orchestrator=SimpleNamespace(),
        client=None,
    )


@pytest.mark.asyncio
async def test_crow_handler_marks_dead_after_repeated_tick_failures() -> None:
    runtime = _Runtime()
    handler = _crow_handler(runtime)
    handler.status = AgentStatus.RUNNING

    await handler._record_tick_failure(RuntimeError("boom 1"))
    await handler._record_tick_failure(RuntimeError("boom 2"))
    await handler._record_tick_failure(RuntimeError("boom 3"))

    assert handler.status == AgentStatus.DEAD
    assert AgentStatus.DEAD in runtime.synced
    assert [getattr(e, "type", None) for e in runtime.bus.events] == [
        "error",
        "error",
        "status_change",
        "error",
    ]
    assert runtime.bus.events[-1].recoverable is False


@pytest.mark.asyncio
async def test_crow_handler_stop_fails_pending_idle_waiters(monkeypatch) -> None:
    runtime = _Runtime()
    handler = _crow_handler(runtime)

    async def fake_kill_session(session: str) -> None:
        del session

    monkeypatch.setattr("murder.tmux.kill_session", fake_kill_session)

    waiter = asyncio.create_task(handler.await_idle())
    await asyncio.sleep(0)
    await handler.stop()

    with pytest.raises(RuntimeError, match="crow_handler stopped"):
        await waiter


@pytest.mark.asyncio
async def test_sentinel_send_to_crow_times_out_waiting_for_idle(monkeypatch) -> None:
    sent: list[str] = []

    class NeverIdleCrowHandler:
        def is_crow_idle(self) -> bool:
            return False

        async def await_idle(self) -> None:
            await asyncio.sleep(10)

    class Crow:
        async def send(self, msg: str) -> None:
            sent.append(msg)

    runtime = SimpleNamespace(
        get_crow_handler=lambda ticket_id: NeverIdleCrowHandler(),
        get_crow=lambda ticket_id: Crow(),
    )
    sentinel = SentinelAgent(
        agent_id="sentinel-0",
        session="sentinel",
        config=SentinelConfig(model="sentinel-model"),
        client=None,
        runtime=runtime,  # type: ignore[arg-type]
        orchestrator=SimpleNamespace(),
    )
    monkeypatch.setattr("murder.agents.sentinel.CROW_IDLE_WAIT_TIMEOUT_S", 0.01)

    result = await sentinel.tool_send_to_crow("t001", "hello")

    assert result == {"error": "crow did not become idle in time"}
    assert sent == []
