"""Notes storage + notetaker capture shim/tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from murder import notes
from murder.agents.notetaker import NotetakerAgent
from murder.clients.base import CompletionResult
from murder.config import NotetakerConfig
from murder.orchestrator import Orchestrator
from murder.runtime import Runtime


class _FenceClient:
    def __init__(self, fence_body: str) -> None:
        self._fence_body = fence_body

    async def complete(self, *, model, system, messages, tools, max_tokens, temperature=0.0):
        del model, system, messages, tools, max_tokens, temperature
        text = "```json\n" + self._fence_body + "\n```"
        return CompletionResult(
            text=text,
            tool_calls=[],
            prompt_tokens=1,
            completion_tokens=1,
            model="fake",
            latency_ms=0.0,
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


# ── shim + capture orchestration ──────────────────────────────────────────


def test_notetaker_agent_shim_errors_on_construct() -> None:
    with pytest.raises(RuntimeError, match="capture.submit"):
        NotetakerAgent()  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_submit_notetaker_capture_inserts_db_and_updates_note(
    monkeypatch: pytest.MonkeyPatch, memdb, tmp_path: Path
) -> None:
    cfg = SimpleNamespace(
        project=SimpleNamespace(name="test"),
        runtime=SimpleNamespace(session_name_template="murder_{project}_{role}{suffix}"),
        notetaker=NotetakerConfig(),
    )
    rt = Runtime(cfg, tmp_path)  # type: ignore[arg-type]
    rt.db = memdb
    monkeypatch.setattr(
        "murder.orchestrator.create_client",
        lambda provider: _FenceClient(
            '{"short_vers": "one item noted"}'
        ),
    )

    out = await Orchestrator(rt).submit_notetaker_capture({"text": "  ramble ramble one  "})

    row = memdb.execute(
        "SELECT raw, cleaned, short_vers FROM notes_entries ORDER BY id DESC LIMIT 1",
    ).fetchone()
    assert row["raw"] == "ramble ramble one"
    assert row["cleaned"] == "ramble ramble one"
    assert out["reply"] == "one item noted"
    merged = notes.read_note(memdb, notes.today_name())
    assert "ramble ramble one" in merged
