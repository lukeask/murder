"""Boundary instrumentation tests: tmux wrapper (#2) + LLM record_completion (#1).

Drives the two Phase-2 instrumentation seams against a REAL in-temp
``AdvancedLog`` (redacted mode) pinned via ``set_current_advanced_log``, with the
underlying ``tmux`` subprocess monkeypatched so no real tmux is needed. Asserts
rows land in ``tmux_frames`` / ``api_records``, then resets the accessor to Null.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from murder.observability.advanced_log import (
    NullAdvancedLog,
    open_advanced_log,
    set_current_advanced_log,
)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".murder").mkdir(parents=True)
    return root


def test_tmux_and_completion_boundaries_record_rows(tmp_path, monkeypatch):
    repo = _repo(tmp_path)

    import murder.runtime.terminal.tmux as tmux

    captured_text = "line-one\nline-two\n"

    async def fake_tmux(*args, check=True, timeout_s=10):
        # capture-pane returns canned frame text; everything else succeeds empty.
        if args and args[0] == "capture-pane":
            return (0, captured_text, "")
        return (0, "", "")

    # session_exists is used by kill_session; force it True so kill issues a call.
    async def fake_exists(name):
        return True

    monkeypatch.setattr(tmux, "_tmux", fake_tmux)
    monkeypatch.setattr(tmux, "session_exists", fake_exists)

    from murder.llm.clients.base import (
        CompletionResult,
        ToolSpec,
        build_request_summary,
        record_completion,
    )

    async def _run() -> Path:
        log = open_advanced_log(repo, "run-instr", "redacted")
        await log.start()
        set_current_advanced_log(log)
        try:
            # --- boundary #2: tmux wrapper ---
            out = await tmux.capture_pane("sess-a", lines=50)
            assert out == captured_text  # behavior preserved
            await tmux.send_keys("sess-a", "hello world", literal=True, enter=True)
            await tmux.kill_session("sess-a")

            # --- boundary #1: LLM record_completion seam ---
            req = build_request_summary(
                model="m-test",
                system="be terse",
                messages=[{"role": "user", "content": "hi"}],
                tools=[ToolSpec(name="t", description="d", parameters={})],
                max_tokens=64,
                temperature=0.0,
            )
            result = CompletionResult(
                text="hello back",
                tool_calls=[],
                prompt_tokens=11,
                completion_tokens=3,
                model="m-test",
                latency_ms=12.5,
            )
            record_completion(request_summary=req, result=result, status="ok", retries=0)
        finally:
            await log.stop()
            set_current_advanced_log(NullAdvancedLog())
        return log._db_path

    try:
        path = asyncio.run(_run())

        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row

        import json

        frames = conn.execute(
            "SELECT session, payload FROM tmux_frames ORDER BY id"
        ).fetchall()
        by_op = {json.loads(r["payload"])["op"]: json.loads(r["payload"]) for r in frames}
        assert {"capture", "send", "kill"} <= set(by_op), set(by_op)
        assert "line-one" in by_op["capture"]["frame"]  # real captured frame body landed
        assert by_op["send"]["frame"] == "hello world"

        api = conn.execute("SELECT model, payload FROM api_records").fetchone()
        assert api is not None
        assert api["model"] == "m-test"
        assert "hello back" in api["payload"]
        assert "be terse" in api["payload"]
        conn.close()
    finally:
        # Belt-and-suspenders: ensure the accessor is Null even if asserts blew up.
        set_current_advanced_log(NullAdvancedLog())


def test_boundaries_are_noop_under_null_writer(tmp_path, monkeypatch):
    """With a Null writer active, the boundaries must not raise and return normally."""
    import murder.runtime.terminal.tmux as tmux

    async def fake_tmux(*args, check=True, timeout_s=10):
        if args and args[0] == "capture-pane":
            return (0, "frame", "")
        return (0, "", "")

    monkeypatch.setattr(tmux, "_tmux", fake_tmux)

    set_current_advanced_log(NullAdvancedLog())
    try:
        out = asyncio.run(tmux.capture_pane("s", lines=10))
        assert out == "frame"
        asyncio.run(tmux.send_keys("s", "x", literal=False))
    finally:
        set_current_advanced_log(NullAdvancedLog())
