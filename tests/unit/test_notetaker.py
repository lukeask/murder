"""NotetakerAgent + notes storage."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from murder import notes
from murder.agents.notetaker import NotetakerAgent
from murder.clients.base import CompletionResult, ToolCall
from murder.config import NotetakerConfig


class _FakeClient:
    """Returns a scripted sequence of completions."""

    def __init__(self, results: list[CompletionResult]) -> None:
        self._results = list(results)
        self.calls: list[list[dict]] = []

    async def complete(self, *, model, system, messages, tools, max_tokens, temperature=0.0):
        del model, system, tools, max_tokens, temperature
        self.calls.append([dict(m) for m in messages])
        return self._results.pop(0)


def _completion(text: str | None, tool_calls: list[ToolCall] | None = None) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        prompt_tokens=1,
        completion_tokens=1,
        model="fake",
        latency_ms=0.0,
    )


def _runtime(memdb):
    return SimpleNamespace(db=memdb, bus=None, run_id=None, sync_agent=lambda agent: None)


def _agent(memdb, tmp_path: Path, client) -> NotetakerAgent:
    return NotetakerAgent(
        agent_id="notetaker-0",
        session="murder_test_notetaker",
        config=NotetakerConfig(),
        client=client,
        repo_root=tmp_path,
        runtime=_runtime(memdb),  # type: ignore[arg-type]
        note_name="2026-05-11",
    )


# ── notes module ───────────────────────────────────────────────────────────


def test_write_note_updates_db_and_materializes_file(memdb, tmp_path: Path) -> None:
    notes.write_note(memdb, tmp_path, "2026-05-11", "# Goals\n- ship it")
    assert notes.read_note(memdb, "2026-05-11") == "# Goals\n- ship it"
    f = tmp_path / ".murder" / "notes" / "2026-05-11.md"
    assert f.read_text(encoding="utf-8") == "# Goals\n- ship it"


def test_latest_prior_note_skips_empty_and_self(memdb, tmp_path: Path) -> None:
    notes.write_note(memdb, tmp_path, "2026-05-09", "old stuff")
    notes.write_note(memdb, tmp_path, "2026-05-10", "")  # empty — should be skipped
    notes.ensure_note(memdb, tmp_path, "2026-05-11")  # today, empty
    prior = notes.latest_prior_note(memdb, exclude="2026-05-11")
    assert prior == ("2026-05-09", "old stuff")


def test_ensure_note_imports_existing_file_without_clobber(memdb, tmp_path: Path) -> None:
    path = tmp_path / ".murder" / "notes" / "2026-05-11.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Existing\n- keep me", encoding="utf-8")

    row = notes.ensure_note(memdb, tmp_path, "2026-05-11")
    assert str(row["body"]) == "# Existing\n- keep me"
    assert notes.read_note(memdb, "2026-05-11") == "# Existing\n- keep me"
    assert path.read_text(encoding="utf-8") == "# Existing\n- keep me"


def test_write_note_records_revisions(memdb, tmp_path: Path) -> None:
    notes.write_note(memdb, tmp_path, "2026-05-11", "v1")
    notes.write_note(memdb, tmp_path, "2026-05-11", "v2")
    revisions = memdb.execute(
        "SELECT source, body FROM note_revisions WHERE note_name = ? ORDER BY id",
        ("2026-05-11",),
    ).fetchall()
    assert [(r["source"], r["body"]) for r in revisions] == [
        ("agent", "v1"),
        ("agent", "v2"),
    ]


# ── NotetakerAgent ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_seeds_simulated_read_when_prior_notes_exist(memdb, tmp_path: Path) -> None:
    notes.write_note(memdb, tmp_path, "2026-05-10", "yesterday's notes")
    agent = _agent(memdb, tmp_path, _FakeClient([]))
    await agent.start("", {})
    assert agent.messages[0]["role"] == "assistant"
    assert agent.messages[0]["tool_calls"][0]["function"]["name"] == "read_notes"
    assert agent.messages[1]["role"] == "tool"
    assert "yesterday's notes" in agent.messages[1]["content"]
    # UI transcript starts with the synthetic "read" line and nothing else yet.
    assert agent.transcript_for_ui() == [("notetaker", "📄 Read current notes (2026-05-11).")]


@pytest.mark.asyncio
async def test_reply_runs_tool_loop_and_writes_notes(memdb, tmp_path: Path) -> None:
    client = _FakeClient(
        [
            _completion(
                "Tidying that up.",
                [ToolCall(name="write_notes", arguments={"content": "## Goals\n- launch"}, call_id="c1")],
            ),
            _completion("Done — want me to break that into tickets?"),
        ]
    )
    agent = _agent(memdb, tmp_path, client)
    await agent.start("", {})
    reply = await agent.reply_to("ok so we wanna launch the thing, goals: launch")
    assert reply == "Done — want me to break that into tickets?"
    assert notes.read_note(memdb, "2026-05-11") == "## Goals\n- launch"
    transcript = agent.transcript_for_ui()
    assert transcript[-2:] == [
        ("you", "ok so we wanna launch the thing, goals: launch"),
        ("notetaker", "Done — want me to break that into tickets?"),
    ]


@pytest.mark.asyncio
async def test_reply_without_client_is_graceful(memdb, tmp_path: Path) -> None:
    agent = _agent(memdb, tmp_path, client=None)
    await agent.start("", {})
    reply = await agent.reply_to("hello?")
    assert "offline" in reply.lower()
    assert agent.messages[-1] == {"role": "assistant", "content": reply}


@pytest.mark.asyncio
async def test_ensure_notetaker_registers_agent_and_persists_row(
    monkeypatch: pytest.MonkeyPatch, memdb, tmp_path: Path
) -> None:
    from murder.orchestrator import Orchestrator
    from murder.runtime import Runtime

    cfg = SimpleNamespace(
        project=SimpleNamespace(name="test"),
        runtime=SimpleNamespace(session_name_template="murder_{project}_{role}{suffix}"),
        notetaker=NotetakerConfig(),
    )
    rt = Runtime(cfg, tmp_path)  # type: ignore[arg-type]
    rt.db = memdb
    monkeypatch.setattr("murder.orchestrator.create_client", lambda provider: None)

    agent_id = await Orchestrator(rt).ensure_notetaker()
    assert agent_id == "notetaker-0"
    assert isinstance(rt.get_agent("notetaker-0"), NotetakerAgent)
    row = memdb.execute(
        "SELECT role, status FROM agents WHERE agent_id = 'notetaker-0'"
    ).fetchone()
    assert (row["role"], row["status"]) == ("notetaker", "running")
    # Re-entrant: same in-memory agent is reused, not re-created.
    assert await Orchestrator(rt).ensure_notetaker() == "notetaker-0"
