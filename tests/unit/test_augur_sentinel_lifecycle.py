from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from murder.agents.augur import AugurAgent
from murder.agents.base import AgentStatus
from murder.agents.sentinel import SentinelAgent
from murder.config import AugurConfig, SentinelConfig
from murder.harnesses.base import HarnessAdapter


class _FakeHarness(HarnessAdapter):
    kind = "fake"
    monkey_system_prompt = ""

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


def _augur(runtime: _Runtime) -> AugurAgent:
    return AugurAgent(
        agent_id="augur-t001",
        ticket_id="t001",
        session="augur-session",
        monkey_session="monkey-session",
        harness=_FakeHarness(),
        config=AugurConfig(model="augur-model", poll_interval_s=0.01),
        repo_root=Path("/repo"),
        runtime=runtime,  # type: ignore[arg-type]
        orchestrator=SimpleNamespace(),
        client=None,
    )


@pytest.mark.asyncio
async def test_augur_marks_dead_after_repeated_tick_failures() -> None:
    runtime = _Runtime()
    augur = _augur(runtime)
    augur.status = AgentStatus.RUNNING

    await augur._record_tick_failure(RuntimeError("boom 1"))
    await augur._record_tick_failure(RuntimeError("boom 2"))
    await augur._record_tick_failure(RuntimeError("boom 3"))

    assert augur.status == AgentStatus.DEAD
    assert AgentStatus.DEAD in runtime.synced
    assert [getattr(e, "type", None) for e in runtime.bus.events] == [
        "error",
        "error",
        "status_change",
        "error",
    ]
    assert runtime.bus.events[-1].recoverable is False


@pytest.mark.asyncio
async def test_augur_stop_fails_pending_idle_waiters(monkeypatch) -> None:
    runtime = _Runtime()
    augur = _augur(runtime)

    async def fake_kill_session(session: str) -> None:
        del session

    monkeypatch.setattr("murder.tmux.kill_session", fake_kill_session)

    waiter = asyncio.create_task(augur.await_idle())
    await asyncio.sleep(0)
    await augur.stop()

    with pytest.raises(RuntimeError, match="augur stopped"):
        await waiter


@pytest.mark.asyncio
async def test_sentinel_send_to_monkey_times_out_waiting_for_idle(monkeypatch) -> None:
    sent: list[str] = []

    class NeverIdleAugur:
        def is_monkey_idle(self) -> bool:
            return False

        async def await_idle(self) -> None:
            await asyncio.sleep(10)

    class Monkey:
        async def send(self, msg: str) -> None:
            sent.append(msg)

    runtime = SimpleNamespace(
        get_augur=lambda ticket_id: NeverIdleAugur(),
        get_monkey=lambda ticket_id: Monkey(),
    )
    sentinel = SentinelAgent(
        agent_id="sentinel-0",
        session="sentinel",
        config=SentinelConfig(model="sentinel-model"),
        client=None,
        runtime=runtime,  # type: ignore[arg-type]
        orchestrator=SimpleNamespace(),
    )
    monkeypatch.setattr("murder.agents.sentinel.MONKEY_IDLE_WAIT_TIMEOUT_S", 0.01)

    result = await sentinel.tool_send_to_monkey("t001", "hello")

    assert result == {"error": "monkey did not become idle in time"}
    assert sent == []
