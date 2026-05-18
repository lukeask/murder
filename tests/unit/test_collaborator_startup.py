from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from murder import db as dbmod
from murder.agents.collaborator import CollaboratorAgent
from murder.bus import AgentStatus
from murder.harnesses.base import HarnessAdapter
from murder.orchestrator import Orchestrator
from murder.runtime import Runtime


class _FakeHarness(HarnessAdapter):
    kind = "fake"
    crow_system_prompt = ""

    def startup_cmd(self, cwd: Path) -> list[str]:
        return ["fake"]

    def is_ready(self, pane_text: str) -> bool:
        return True

    def is_idle(self, pane_text: str) -> bool:
        return True

    def is_busy(self, pane_text: str) -> bool:
        return False

    def extract_last_message(self, pane_text: str) -> str | None:
        return pane_text or None

    def format_nudge(self, msg: str) -> str:
        return msg


def _runtime(memdb, tmp_path: Path) -> Runtime:
    cfg = SimpleNamespace(
        project=SimpleNamespace(name="test"),
        runtime=SimpleNamespace(session_name_template="murder_{project}_{role}{suffix}"),
        collaborator=SimpleNamespace(
            startup_model=None,
            harness="claude_code",
            startup_prompt_template="collaborator.md",
        ),
    )
    rt = Runtime(cfg, tmp_path)  # type: ignore[arg-type]
    rt.db = memdb
    return rt


@pytest.mark.asyncio
async def test_failed_collaborator_start_is_reaped(
    monkeypatch: pytest.MonkeyPatch, memdb, tmp_path: Path
) -> None:
    rt = _runtime(memdb, tmp_path)
    orch = Orchestrator(rt)
    stopped: list[str] = []

    monkeypatch.setattr("murder.orchestrator.get_harness", lambda *a, **k: _FakeHarness())
    monkeypatch.setattr("murder.orchestrator.load", lambda name: "startup prompt")

    async def fail_start(self: CollaboratorAgent, brief, ctx) -> None:
        del brief, ctx
        raise TimeoutError("not ready")

    async def record_stop(self: CollaboratorAgent) -> None:
        stopped.append(self.id)

    monkeypatch.setattr(CollaboratorAgent, "start", fail_start)
    monkeypatch.setattr(CollaboratorAgent, "stop", record_stop)

    with pytest.raises(TimeoutError):
        await orch.ensure_collaborator()

    assert rt.get_agent("collaborator-0") is None
    assert stopped == ["collaborator-0"]
    row = memdb.execute("SELECT status FROM agents WHERE agent_id = 'collaborator-0'").fetchone()
    assert row["status"] == AgentStatus.DEAD.value


@pytest.mark.asyncio
async def test_stale_collaborator_row_does_not_block_retry(
    monkeypatch: pytest.MonkeyPatch, memdb, tmp_path: Path
) -> None:
    rt = _runtime(memdb, tmp_path)
    orch = Orchestrator(rt)
    starts: list[str] = []

    dbmod.upsert_agent(
        memdb,
        agent_id="collaborator-stale",
        role="collaborator",
        ticket_id=None,
        session="dead-session",
        status=AgentStatus.IDLE.value,
    )

    monkeypatch.setattr("murder.orchestrator.get_harness", lambda *a, **k: _FakeHarness())
    monkeypatch.setattr("murder.orchestrator.load", lambda name: "startup prompt")

    async def ok_start(self: CollaboratorAgent, brief, ctx) -> None:
        del brief, ctx
        starts.append(self.id)
        self.status = AgentStatus.RUNNING
        if self.runtime:
            self.runtime.sync_agent(self)

    monkeypatch.setattr(CollaboratorAgent, "start", ok_start)

    agent_id = await orch.ensure_collaborator()

    assert agent_id == "collaborator-0"
    assert starts == ["collaborator-0"]
    stale = memdb.execute(
        "SELECT status FROM agents WHERE agent_id = 'collaborator-stale'"
    ).fetchone()
    fresh = memdb.execute("SELECT status FROM agents WHERE agent_id = 'collaborator-0'").fetchone()
    assert stale["status"] == AgentStatus.DEAD.value
    assert fresh["status"] == AgentStatus.RUNNING.value
