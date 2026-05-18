"""Orchestrator ``notetaker.capture.submit`` plus capture DB/raw merge tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from murder import notes as notes_mod
from murder import notetaker_capture
from murder.bus.protocol import CommandEvent
from murder.clients.base import CompletionResult
from murder.config import NotetakerConfig
from murder.workers.base import WorkerCtx
from murder.workers.orchestrator_worker import OrchestratorCommandWorker


@pytest.mark.asyncio
async def test_notetaker_capture_submit_command() -> None:
    submitted: list[dict[str, object]] = []

    async def _kickoff_ready(_only: str | None) -> list[str]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _apply_carve_ready(_tid: str, _payload: dict[str, object]) -> dict[str, object]:
        raise AssertionError("should not be called")

    async def _capture_submit(payload: dict[str, object]) -> dict[str, object]:
        submitted.append(dict(payload))
        return {
            "entry_id": 7,
            "cleaned": "## X",
            "short_vers": "ok",
            "reply": "ok",
        }

    async def _retry_failed(_ticket_id: str) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _set_schedule_at(
        _ticket_id: str, _schedule_at: str | None
    ) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _update_metadata(
        _t: str, _p: dict[str, object]
    ) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    async def _force_status(_t: str, _s: str) -> dict[str, object]:  # pragma: no cover
        raise AssertionError("should not be called")

    worker = OrchestratorCommandWorker(
        kickoff_ready=_kickoff_ready,
        apply_carve_ready=_apply_carve_ready,
        capture_submit=_capture_submit,
        retry_failed=_retry_failed,
        set_schedule_at=_set_schedule_at,
        update_metadata=_update_metadata,
        force_status=_force_status,
    )
    result = await worker.on_command(
        CommandEvent(
            run_id="r1",
            target_worker="orchestrator",
            kind="notetaker.capture.submit",
            payload={"text": "hello"},
            correlation_id="corr-1",
            idempotency_key="idem-1",
        ),
        WorkerCtx(repo_root=Path(".")),
    )

    assert submitted == [{"text": "hello"}]
    assert result == {
        "handled": True,
        "entry_id": 7,
        "cleaned": "## X",
        "short_vers": "ok",
        "reply": "ok",
    }


class _BadJsonClient:
    async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        return CompletionResult(
            text="not json at all",
            tool_calls=[],
            prompt_tokens=0,
            completion_tokens=0,
            model="x",
            latency_ms=0.0,
        )


@pytest.mark.asyncio
async def test_submit_capture_llm_failure_raw_cleaned_and_fallback_short_vers(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    raw_in = "  hello world  "
    body = "hello world"
    fallback = "hello world"

    out = await notetaker_capture.submit_capture(
        repo_root=tmp_path,
        conn=memdb,
        raw=raw_in,
        client=_BadJsonClient(),
        config=NotetakerConfig(),
        note_name="2099-01-01",
    )

    row = memdb.execute(
        "SELECT raw, cleaned, short_vers FROM notes_entries ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["raw"] == body
    assert row["cleaned"] == body
    assert row["short_vers"] == fallback
    assert out["cleaned"] == body
    assert out["short_vers"] == fallback


class _HappyShortVersClient:
    async def complete(self, **kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        text = '```json\n{"short_vers": "LLM sentence for chat ack."}\n```'
        return CompletionResult(
            text=text,
            tool_calls=[],
            prompt_tokens=1,
            completion_tokens=1,
            model="fake",
            latency_ms=0.0,
        )


@pytest.mark.asyncio
async def test_submit_capture_happy_path_raw_cleaned_and_llm_short_vers(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    raw_in = "  my capture text  "
    body = "my capture text"

    out = await notetaker_capture.submit_capture(
        repo_root=tmp_path,
        conn=memdb,
        raw=raw_in,
        client=_HappyShortVersClient(),
        config=NotetakerConfig(),
        note_name="2099-01-02",
    )

    row = memdb.execute(
        "SELECT raw, cleaned, short_vers FROM notes_entries ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row["raw"] == body
    assert row["cleaned"] == body
    assert row["short_vers"] == "LLM sentence for chat ack."
    assert out["cleaned"] == body
    assert out["short_vers"] == "LLM sentence for chat ack."

    merged = notes_mod.read_note(memdb, "2099-01-02")
    assert body in merged
