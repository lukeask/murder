"""Tests for HarnessBackedAgent._sync_lifecycle_status (BUG-13).

The crows-panel spinner is derived from ``agents.status == 'running'``. Prior
to the fix a crow set RUNNING on startup and only left it on stop, so a finished
crow stayed "running" forever. _sync_lifecycle_status reconciles the
process-lifecycle status with the harness's working↔idle signal each projection
tick — edge-triggered (only syncs on change) and strictly a RUNNING↔IDLE toggle
(never clobbers blocked/escalating/done/failed/dead).
"""

from __future__ import annotations

from murder.runtime.agents.base import AgentStatus, HarnessBackedAgent


class _FakeRuntime:
    def __init__(self) -> None:
        self.sync_calls: list[str] = []

    def sync_agent(self, agent: object) -> None:  # noqa: ANN401
        self.sync_calls.append(getattr(agent, "status").value)


class _StubAgent(HarnessBackedAgent):
    """Minimal harness-backed agent exposing only what the status sync reads."""

    role = None  # type: ignore[assignment]

    def __init__(self, *, status: AgentStatus, live_state: str | None) -> None:
        self.id = "crow-t001"
        self.ticket_id = "t001"
        self.session = "crow-t001"
        self.status = status
        self.runtime = _FakeRuntime()
        self._live_state = live_state

    # Override the producer-backed accessor with a directly-settable signal.
    def _current_live_state(self) -> str | None:  # type: ignore[override]
        return self._live_state

    async def start(self, brief, ctx):  # pragma: no cover - unused
        raise NotImplementedError

    async def stop(self, *, failed=False, kill_session=True):  # pragma: no cover
        raise NotImplementedError

    async def send(self, msg):  # pragma: no cover - unused
        raise NotImplementedError


def test_working_to_idle_flips_running_to_idle_once() -> None:
    agent = _StubAgent(status=AgentStatus.RUNNING, live_state="awaiting_input")
    agent._sync_lifecycle_status()
    assert agent.status == AgentStatus.IDLE
    assert agent.runtime.sync_calls == ["idle"]


def test_idle_to_working_flips_back() -> None:
    agent = _StubAgent(status=AgentStatus.IDLE, live_state="working")
    agent._sync_lifecycle_status()
    assert agent.status == AgentStatus.RUNNING
    assert agent.runtime.sync_calls == ["running"]


def test_awaiting_approval_counts_as_idle() -> None:
    agent = _StubAgent(status=AgentStatus.RUNNING, live_state="awaiting_approval")
    agent._sync_lifecycle_status()
    assert agent.status == AgentStatus.IDLE
    assert agent.runtime.sync_calls == ["idle"]


def test_blocked_agent_not_flipped_by_idle_signal() -> None:
    agent = _StubAgent(status=AgentStatus.BLOCKED, live_state="awaiting_input")
    agent._sync_lifecycle_status()
    assert agent.status == AgentStatus.BLOCKED
    assert agent.runtime.sync_calls == []


def test_escalating_agent_not_flipped_by_idle_signal() -> None:
    agent = _StubAgent(status=AgentStatus.ESCALATING, live_state="awaiting_input")
    agent._sync_lifecycle_status()
    assert agent.status == AgentStatus.ESCALATING
    assert agent.runtime.sync_calls == []


def test_done_agent_stays_done() -> None:
    agent = _StubAgent(status=AgentStatus.DONE, live_state="working")
    agent._sync_lifecycle_status()
    assert agent.status == AgentStatus.DONE
    assert agent.runtime.sync_calls == []


def test_no_redundant_sync_when_state_unchanged() -> None:
    agent = _StubAgent(status=AgentStatus.RUNNING, live_state="working")
    agent._sync_lifecycle_status()
    agent._sync_lifecycle_status()
    assert agent.status == AgentStatus.RUNNING
    assert agent.runtime.sync_calls == []


def test_none_live_state_is_a_noop() -> None:
    agent = _StubAgent(status=AgentStatus.RUNNING, live_state=None)
    agent._sync_lifecycle_status()
    assert agent.status == AgentStatus.RUNNING
    assert agent.runtime.sync_calls == []
