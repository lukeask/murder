from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from murder.runtime.agents.collaborator import CollaboratorAgent
from murder.llm.harnesses.claude_code import ClaudeCodeAdapter
from murder.state.persistence.schema import get_db, init_db
from murder.runtime.terminal import tmux
from tests.support.fake_tmux import FakeTmux

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "harness_panes"
CC_IDLE = (_FIXTURES / "cc_idle.txt").read_text(encoding="utf-8")


def test_collaborator_start_clears_prior_conversation(
    fake_tmux: FakeTmux,
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def _session_exists(_session: str) -> bool:
        return True

    monkeypatch.setattr(tmux, "session_exists", _session_exists)
    fake_tmux.queue_pane(CC_IDLE)
    conn = get_db(tmp_path / "state.db")
    init_db(conn)
    conn.execute(
        "INSERT INTO agent_messages(agent_id, ordinal, role, body, captured_at) "
        "VALUES ('collaborator-0', 0, 'user', 'stale', '2026-06-02T00:00:00Z')"
    )
    runtime = SimpleNamespace(db=conn, bus=None, run_id=None, sync_agent=MagicMock())
    agent = CollaboratorAgent(
        agent_id="collaborator-0",
        session="murder_test_collaborator",
        harness=ClaudeCodeAdapter(),
        repo_root=tmp_path,
        runtime=runtime,
    )

    asyncio.run(agent.start("fresh brief", {}))

    rows = conn.execute(
        "SELECT body FROM agent_messages WHERE agent_id = 'collaborator-0'"
    ).fetchall()
    assert rows == []
    runtime.sync_agent.assert_called_once_with(agent)
