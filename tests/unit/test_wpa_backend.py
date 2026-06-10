"""WP-A backend coverage (dogfood items 8, 3, 3b/10).

Driven with ``asyncio.run`` (NOT ``@pytest.mark.asyncio``) so the bodies actually
execute under this repo's pytest config (no ``asyncio_mode = auto``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from murder.app.service.runtime import Runtime
from murder.bus import Bus
from murder.config import (
    Config,
    CrowHandlerConfig,
    HarnessRoleConfig,
    NotetakerConfig,
    ProjectConfig,
)
from murder.runtime.orchestration.orchestrator import Orchestrator
from murder.state.persistence.schema import get_db, init_db
from murder.work import notes as notes_mod


def _config() -> Config:
    return Config(
        project=ProjectConfig(name="repo"),
        collaborator=HarnessRoleConfig(harness="codex"),
        default_crow=HarnessRoleConfig(harness="codex"),
        crow_handler=CrowHandlerConfig(model="test-model"),
        notetaker=NotetakerConfig(model="test-model"),
    )


def _runtime(repo_root: Path) -> Runtime:
    conn = get_db(repo_root / ".murder" / "murder.db")
    init_db(conn)
    rt = Runtime(_config(), repo_root)
    rt.db = conn
    rt.run_id = "run-test"
    rt.bus = Bus(rt.run_id, conn)
    return rt


async def _drain(rt: Runtime) -> None:
    if rt._emit_tasks:
        await asyncio.gather(*list(rt._emit_tasks))


# === Item 8: spawn_if_needed gate ============================================


def test_send_message_no_spawn_when_planner_not_live(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    orch = Orchestrator(rt)

    spawned: list[str] = []

    async def _fake_ensure(plan_name: str):
        spawned.append(plan_name)

    orch.ensure_planning_agent = _fake_ensure  # type: ignore[assignment]

    result = asyncio.run(
        orch.send_agent_message("planner-demo", "your plan is malformed", None, spawn_if_needed=False)
    )

    assert result["handled"] is False
    assert result["reason"] == "agent-not-live"
    assert spawned == []


def test_send_message_spawns_when_planner_not_live_by_default(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    orch = Orchestrator(rt)

    spawned: list[str] = []

    async def _fake_ensure(plan_name: str):
        spawned.append(plan_name)

    orch.ensure_planning_agent = _fake_ensure  # type: ignore[assignment]

    # No agent registered after ensure -> falls through to the no-agent branch,
    # but ensure_planning_agent must have been invoked (spawn path taken).
    result = asyncio.run(orch.send_agent_message("planner-demo", "hi", None))

    assert spawned == ["demo"]
    assert result["handled"] is False  # no live agent materialized in the fake


def test_send_message_delivers_to_live_planner_without_spawn(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    orch = Orchestrator(rt)

    spawned: list[str] = []

    async def _fake_ensure(plan_name: str):
        spawned.append(plan_name)

    orch.ensure_planning_agent = _fake_ensure  # type: ignore[assignment]

    sent: list[str] = []

    class _FakeAgent:
        id = "planner-demo"
        ticket_id = None
        role = None

        async def send(self, message: str):
            sent.append(message)
            return None

    agent = _FakeAgent()
    rt._agents._agents["planner-demo"] = agent  # type: ignore[attr-defined]

    async def _live(_agent) -> bool:
        return True

    orch._agent_is_live = _live  # type: ignore[assignment]

    result = asyncio.run(
        orch.send_agent_message("planner-demo", "malformed", None, spawn_if_needed=False)
    )

    assert spawned == []
    assert sent == ["malformed"]
    assert result["handled"] is True


# === Item 3: plan.create auto_name + body ====================================


def test_create_plan_with_body_seeds_markdown(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    orch = Orchestrator(rt)

    body = "# Custom Plan\n\nSeeded body content.\n"
    result = asyncio.run(orch.create_plan("seeded", "", body=body))
    asyncio.run(_drain(rt))

    assert result["ok"] is True
    assert result["plan_name"] == "seeded"
    row = rt.db.execute("SELECT body FROM plans WHERE name = 'seeded'").fetchone()
    assert "Seeded body content." in row["body"]


def test_create_plan_auto_name_falls_back_to_timestamp_slug(repo_root: Path) -> None:
    # No real LLM client is configured for the test config, so auto_name must
    # fall back to a timestamp slug rather than raising.
    rt = _runtime(repo_root)
    orch = Orchestrator(rt)

    result = asyncio.run(
        orch.create_plan(None, "", body="A plan about refactoring the parser.", auto_name=True)
    )
    asyncio.run(_drain(rt))

    assert result["ok"] is True
    name = result["plan_name"]
    assert name.startswith("plan-")
    assert rt.db.execute("SELECT 1 FROM plans WHERE name = ?", (name,)).fetchone() is not None


def test_create_plan_auto_name_uses_llm_slug(repo_root: Path, monkeypatch) -> None:
    rt = _runtime(repo_root)
    orch = Orchestrator(rt)

    async def _fake_meta(**kwargs):
        return {"short_vers": "x", "one_or_two_word_title": "Parser Rewrite"}

    monkeypatch.setattr(notes_mod, "llm_capture_metadata", _fake_meta)
    monkeypatch.setattr(
        "murder.runtime.orchestration.orchestrator.resolve_role_client",
        lambda cfg: object(),
    )

    result = asyncio.run(
        orch.create_plan(None, "", body="Rewrite the parser substrate.", auto_name=True)
    )
    asyncio.run(_drain(rt))

    assert result["plan_name"] == "parser-rewrite"


# === Item 3b/10: capture submit custom title =================================


def test_submit_capture_custom_title_skips_llm(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    orch = Orchestrator(rt)

    # No client configured -> if the LLM path were taken it would fall back; the
    # custom-title path must instead use the slugified title. Guard by failing
    # if the LLM titling helper is ever called.
    called: list[bool] = []

    async def _boom(**kwargs):
        called.append(True)
        return {"short_vers": "nope", "one_or_two_word_title": "nope"}

    orig = notes_mod.llm_capture_metadata
    notes_mod.llm_capture_metadata = _boom  # type: ignore[assignment]
    try:
        result = asyncio.run(
            orch.submit_notetaker_capture(
                {"raw": "First line of capture\nsecond line", "title": "My Custom Title"}
            )
        )
        asyncio.run(_drain(rt))
    finally:
        notes_mod.llm_capture_metadata = orig  # type: ignore[assignment]

    assert called == []
    assert result["note_name"] == "my-custom-title"
    assert result["short_vers"] == "First line of capture"


def test_submit_capture_blank_title_uses_llm_path(repo_root: Path) -> None:
    rt = _runtime(repo_root)
    orch = Orchestrator(rt)

    seen: list[dict] = []

    async def _fake_meta(**kwargs):
        seen.append(kwargs)
        return {"short_vers": "summary", "one_or_two_word_title": ""}

    orig = notes_mod.llm_capture_metadata
    notes_mod.llm_capture_metadata = _fake_meta  # type: ignore[assignment]
    try:
        result = asyncio.run(
            orch.submit_notetaker_capture({"raw": "some capture", "title": "   "})
        )
        asyncio.run(_drain(rt))
    finally:
        notes_mod.llm_capture_metadata = orig  # type: ignore[assignment]

    # Blank title is treated as absent -> LLM titling path runs.
    assert len(seen) == 1
    assert result["short_vers"] == "summary"
