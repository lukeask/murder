"""murda must work on crows the runtime no longer tracks (post-restart zombies).

The roster derives "running" from the ``agents`` table, while ``stop_agent``
previously only consulted the in-memory registry — so a crow spawned in a prior
service run reported "no agent named X" when murdered. ``stop_agent`` now falls
back to tearing the tmux session down and marking the DB row dead.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.state.persistence.connection import RepoDb
from tests.support.database import open_test_repo_db


@dataclass
class _Runtime:
    repo_root: Path
    config: Config
    db: object

    def get_agent(self, _agent_id: str):
        return None

    def get_crow(self, _ticket_id: str):
        return None

    def get_crow_handler(self, _ticket_id: str):
        return None

    async def reap(self, _agent_id: str) -> None:  # pragma: no cover - unused here
        raise AssertionError("reap should not be called for an unregistered agent")


def _config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


def _insert_agent(db: RepoDb, agent_id: str, session: str, status: str = "running") -> None:
    db.conn.execute(
        """
        INSERT INTO agents(repository_id, agent_id, role, status, session, started_at)
        VALUES (?, ?, 'crow', ?, ?, '2026-01-01')
        """,
        (db.repository_id, agent_id, status, session),
    )


def test_stop_unregistered_rogue_kills_session_and_marks_dead(
    repo_root: Path, monkeypatch
) -> None:
    db = open_test_repo_db(repo_root / "murder.db")
    _insert_agent(db, "codex-rogue-planreview", "murder_repo_crow_codex_rogue_planreview")
    rt = _Runtime(repo_root=repo_root, config=_config(), db=db)

    killed: list[str] = []

    async def fake_exists(name: str) -> bool:
        return True

    async def fake_kill(name: str) -> None:
        killed.append(name)

    monkeypatch.setattr(
        "murder.runtime.orchestration.orchestrator.tmux.session_exists", fake_exists
    )
    monkeypatch.setattr("murder.runtime.orchestration.orchestrator.tmux.kill_session", fake_kill)

    result = asyncio.run(Orchestrator(rt).stop_agent("codex-rogue-planreview"))  # type: ignore[arg-type]

    assert result == {"handled": True, "agent_id": "codex-rogue-planreview"}
    assert killed == ["murder_repo_crow_codex_rogue_planreview"]
    # Read back through a *fresh* connection — the read_model that feeds the
    # roster opens its own connection per call, so the write must be committed
    # (autocommit/WAL) or the murdered crow would linger as RUNNING.
    fresh = open_test_repo_db(repo_root / "murder.db")
    status = fresh.conn.execute(
        "SELECT status FROM agents WHERE repository_id = ? AND agent_id = ?",
        (fresh.repository_id, "codex-rogue-planreview"),
    ).fetchone()["status"]
    assert status == "dead"


def test_stop_unregistered_crow_also_reaps_handler(repo_root: Path, monkeypatch) -> None:
    db = open_test_repo_db(repo_root / "murder.db")
    _insert_agent(db, "crow-t001", "murder_repo_crow_t001")
    db.conn.execute(
        """
        INSERT INTO agents(repository_id, agent_id, role, status, session, started_at)
        VALUES (?, 'crow_handler-t001', 'crow_handler', 'running', NULL, '2026-01-01')
        """,
        (db.repository_id,),
    )
    rt = _Runtime(repo_root=repo_root, config=_config(), db=db)

    async def fake_exists(name: str) -> bool:
        return True

    async def fake_kill(name: str) -> None:
        return None

    monkeypatch.setattr(
        "murder.runtime.orchestration.orchestrator.tmux.session_exists", fake_exists
    )
    monkeypatch.setattr("murder.runtime.orchestration.orchestrator.tmux.kill_session", fake_kill)

    result = asyncio.run(Orchestrator(rt).stop_agent("crow-t001"))  # type: ignore[arg-type]

    assert result["handled"] is True
    rows = {
        r["agent_id"]: r["status"]
        for r in db.conn.execute(
            "SELECT agent_id, status FROM agents WHERE repository_id = ?",
            (db.repository_id,),
        ).fetchall()
    }
    assert rows == {"crow-t001": "dead", "crow_handler-t001": "dead"}


def test_stop_truly_unknown_agent_reports_real_error(repo_root: Path) -> None:
    db = open_test_repo_db(repo_root / "murder.db")
    rt = _Runtime(repo_root=repo_root, config=_config(), db=db)

    result = asyncio.run(Orchestrator(rt).stop_agent("nope-rogue-ghost"))  # type: ignore[arg-type]

    assert result == {"ok": False, "error": "no agent named nope-rogue-ghost"}
