"""F3 — the 3 genuinely-missing backend RPCs.

Covers the orchestrator methods backing ``ticket.save_body``, ``ticket.schedule``
and ``plan.create`` (the host.py handlers are thin arg-validating wrappers over
these). Each mutating method must also emit the matching key-only
``state.snapshot{entity}`` per the F1 contract.

Test harness mirrors ``test_f1_keyonly_snapshot_ticket.py``: a real Runtime with
an in-memory-ish sqlite db + OrchestrationNotifier, drained explicitly (conftest noop-patches
``asyncio.sleep`` so we never spin a poll loop).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from murder.app.service.runtime import Runtime
from murder.app.protocol.requests import CommandName
from murder.bus import OrchestrationNotifier
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    ProjectConfig,
)
from murder.state.persistence.runs import insert_run
from murder.state.persistence.schema import get_db, init_db
from murder.state.storage.paths import plan_md, ticket_md
from murder.work.tickets.parser import parse_ticket

from tests.unit.test_ticket_sync_unified import _insert_ticket


def _config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
    )


def _runtime(repo_root: Path) -> Runtime:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    rt = Runtime(_config(), repo_root)
    rt.db = conn
    rt.run_id = "run-test"
    insert_run(conn, rt.run_id, "{}")
    rt.bus = OrchestrationNotifier(rt.run_id, conn)
    return rt


def _orch(rt: Runtime):
    from murder.runtime.orchestration.orchestrator import Orchestrator

    return Orchestrator(rt)


# === ticket.save_body =======================================================


@pytest.mark.asyncio
async def test_save_body_persists_body_and_preserves_frontmatter(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    _insert_ticket(rt.db, "t000", title="Dep")
    _insert_ticket(rt.db, "t001", title="Keep me", harness="cursor", model="opus")
    path = ticket_md(repo_root, "t001")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntitle: Keep me\ndeps: [t000]\nharness: cursor\nmodel: opus\n"
        "worktree: feat-x\n---\n# Old\n\n# Checklist\n[ ] old item\n",
        encoding="utf-8",
    )

    orch = _orch(rt)
    new_body = "## Plan\n\nfresh plan prose\n\n# Checklist\n[x] new item\n"
    result = await orch.save_ticket_body("t001", new_body)

    assert result == {"handled": True, "ok": True, "ticket_id": "t001"}
    # Body written; read-only frontmatter (title/harness/model/worktree/deps) preserved.
    parsed = parse_ticket(path.read_text(encoding="utf-8"), default_title="t001")
    assert parsed.title == "Keep me"
    assert parsed.harness == "cursor"
    assert parsed.model == "opus"
    assert parsed.worktree == "feat-x"
    assert parsed.deps == ["t000"]
    assert "fresh plan prose" in path.read_text(encoding="utf-8")
    # Reconciled into the DB: the new checklist item replaced the old one.
    texts = [
        str(r["text"])
        for r in rt.db.execute(
            "SELECT text, done FROM checklist WHERE ticket_id = ? ORDER BY ord", ("t001",)
        ).fetchall()
    ]
    assert texts == ["new item"]


@pytest.mark.asyncio
async def test_save_body_appends_schedule_projection_input(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    _insert_ticket(rt.db, "t002")

    await _orch(rt).save_ticket_body("t002", "# Checklist\n[ ] a\n")

    row = rt.db.execute(
        "SELECT projection, subject_key, generation FROM projection_inputs "
        "WHERE projection = 'schedule' AND subject_key = 't002' "
        "ORDER BY generation DESC LIMIT 1"
    ).fetchone()
    assert dict(row) == {"projection": "schedule", "subject_key": "t002", "generation": 0}


@pytest.mark.asyncio
async def test_save_body_missing_ticket_no_file_returns_error(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    result = await _orch(rt).save_ticket_body("t404", "# Checklist\n")
    assert result["ok"] is False
    assert "not found" in result["error"]


# === ticket.schedule ========================================================


@pytest.mark.asyncio
async def test_schedule_sets_future_timestamp(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    _insert_ticket(rt.db, "t010")

    before = datetime.utcnow()
    result = await _orch(rt).schedule_ticket("t010", "1h30m")

    assert result["handled"] is True
    stored = rt.db.execute(
        "SELECT schedule_at FROM tickets WHERE id = ?", ("t010",)
    ).fetchone()["schedule_at"]
    parsed = datetime.fromisoformat(stored)
    # ~1h30m ahead of the call (allow generous slack for test runtime).
    assert timedelta(minutes=85) <= (parsed - before) <= timedelta(minutes=95)


@pytest.mark.asyncio
async def test_schedule_empty_duration_clears(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    _insert_ticket(rt.db, "t011")
    rt.db.execute("UPDATE tickets SET schedule_at = ? WHERE id = ?", ("2030-01-01T00:00:00", "t011"))
    rt.db.commit()

    result = await _orch(rt).schedule_ticket("t011", "  ")

    assert result["schedule_at"] is None
    stored = rt.db.execute(
        "SELECT schedule_at FROM tickets WHERE id = ?", ("t011",)
    ).fetchone()["schedule_at"]
    assert stored is None


@pytest.mark.asyncio
async def test_schedule_invalid_duration_raises(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    _insert_ticket(rt.db, "t012")
    with pytest.raises(ValueError):
        await _orch(rt).schedule_ticket("t012", "not-a-duration")


@pytest.mark.asyncio
async def test_schedule_emits_ticket_snapshot(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    _insert_ticket(rt.db, "t013")
    await _orch(rt).schedule_ticket("t013", "30m")
    row = rt.db.execute(
        "SELECT subject_key FROM projection_inputs WHERE projection = 'schedule' "
        "AND subject_key = 't013'"
    ).fetchone()
    assert row is not None


# === plan.create ============================================================


@pytest.mark.asyncio
async def test_create_plan_no_message_scaffolds_and_emits(repo_root: Path) -> None:
    rt = _runtime(repo_root)

    result = await _orch(rt).create_plan("my-plan", "")

    assert result == {"handled": True, "ok": True, "plan_name": "my-plan", "agent_id": None}
    assert plan_md(repo_root, "my-plan").exists()
    row = rt.db.execute("SELECT name FROM plans WHERE name = ?", ("my-plan",)).fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_create_plan_with_message_seeds_planner(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    orch = _orch(rt)
    sent: list[tuple[str, str]] = []

    async def _fake_send(agent_id: str, message: str, ticket_id):
        sent.append((agent_id, message))
        return {"handled": True}

    orch.send_agent_message = _fake_send  # type: ignore[assignment]

    result = await orch.create_plan("seeded-plan", "  build the thing  ")

    assert result == {
        "handled": True,
        "ok": True,
        "plan_name": "seeded-plan",
        "agent_id": "planner-seeded-plan",
    }
    assert sent == [("planner-seeded-plan", "build the thing")]


@pytest.mark.asyncio
async def test_create_plan_blank_name_raises(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    with pytest.raises(ValueError):
        await _orch(rt).create_plan("   ", "msg")


# === host.py RPC registration + dispatch wrappers ===========================


class _StubOrch:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def save_ticket_body(self, ticket_id: str, body: str) -> dict[str, object]:
        self.calls.append(("save_ticket_body", ticket_id, body))
        return {"handled": True, "ok": True, "ticket_id": ticket_id}

    async def schedule_ticket(self, ticket_id: str, duration: str) -> dict[str, object]:
        self.calls.append(("schedule_ticket", ticket_id, duration))
        return {"handled": True, "ticket_id": ticket_id, "schedule_at": None}

    async def create_plan(
        self,
        plan_name: str,
        message: str,
        *,
        body: str | None = None,
        auto_name: bool = False,
    ) -> dict[str, object]:
        self.calls.append(("create_plan", plan_name, message))
        return {"handled": True, "plan_name": plan_name, "agent_id": None}


def _host_with_handlers(repo_root: Path):
    from murder.app.service.host import ServiceHost

    host = ServiceHost(config=_config(), repo_root=repo_root)
    host.register_application_handlers()
    host.orchestrator = _StubOrch()  # type: ignore[assignment]
    return host


def test_host_registers_the_three_typed_capabilities(repo_root: Path) -> None:
    host = _host_with_handlers(repo_root)
    from murder.app.protocol.requests import CommandName

    for capability in (
        CommandName.TICKET_SAVE_BODY,
        CommandName.TICKET_SCHEDULE,
        CommandName.PLAN_CREATE,
    ):
        assert capability in host._application_commands


@pytest.mark.asyncio
async def test_host_dispatch_routes_to_orchestrator(repo_root: Path) -> None:
    host = _host_with_handlers(repo_root)
    stub = host.orchestrator

    from murder.app.protocol.requests import CommandName

    await host._application_commands[CommandName.TICKET_SAVE_BODY]({"ticket_id": "t1", "body": "x"})
    await host._application_commands[CommandName.TICKET_SCHEDULE]({"ticket_id": "t1", "duration": "1h"})
    await host._application_commands[CommandName.PLAN_CREATE]({"plan_name": "p", "message": "go"})

    assert stub.calls == [  # type: ignore[attr-defined]
        ("save_ticket_body", "t1", "x"),
        ("schedule_ticket", "t1", "1h"),
        ("create_plan", "p", "go"),
    ]


@pytest.mark.asyncio
async def test_host_dispatch_validates_required_args(repo_root: Path) -> None:
    host = _host_with_handlers(repo_root)
    with pytest.raises(ValueError):
        await host._application_commands[CommandName.TICKET_SAVE_BODY]({"ticket_id": "", "body": "x"})
    with pytest.raises(ValueError):
        await host._application_commands[CommandName.TICKET_SAVE_BODY]({"ticket_id": "t1"})  # body not str
    with pytest.raises(ValueError):
        await host._application_commands[CommandName.TICKET_SCHEDULE]({"ticket_id": "  "})
    with pytest.raises(ValueError):
        await host._application_commands[CommandName.PLAN_CREATE]({"plan_name": " ", "message": "m"})
