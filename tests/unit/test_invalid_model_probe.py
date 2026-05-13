from __future__ import annotations

from pathlib import Path

import pytest

from murder.harnesses.claude_code import ClaudeCodeAdapter
from murder.harnesses.codex import CodexAdapter
from murder.harnesses.cursor import CursorAdapter


@pytest.mark.parametrize(
    "adapter",
    [CursorAdapter(), ClaudeCodeAdapter(), CodexAdapter()],
    ids=["cursor", "claude_code", "codex"],
)
async def test_invalid_model_probe_sends_slash_model_and_detects_rejection(
    monkeypatch, adapter
) -> None:
    calls: list[tuple[str, str, bool, bool]] = []
    sleeps: list[float] = []

    async def fake_send_keys(
        session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        calls.append((session, text, literal, enter))

    async def fake_capture_pane(session: str, lines: int = 200) -> str:
        assert session == "sess"
        assert lines == 200
        return "Error: thisisnotarealmodel is unsupported"

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    monkeypatch.setattr("murder.tmux.capture_pane", fake_capture_pane)
    monkeypatch.setattr("murder.harnesses.base.asyncio.sleep", fake_sleep)

    result = await adapter.attach("sess", Path("/repo")).probe_invalid_model(
        "thisisnotarealmodel"
    )

    assert result.ok, f"rejection detection issue: {result.message}"
    assert calls == [("sess", "/model thisisnotarealmodel", True, True)]
    assert sleeps == [adapter.model_selection_capture_delay_s]


@pytest.mark.parametrize(
    "adapter",
    [CursorAdapter(), ClaudeCodeAdapter(), CodexAdapter()],
    ids=["cursor", "claude_code", "codex"],
)
async def test_invalid_model_probe_fails_when_pane_has_no_rejection(
    monkeypatch, adapter
) -> None:
    async def fake_send_keys(
        session: str, text: str, *, literal: bool = True, enter: bool = True
    ) -> None:
        del session, text, literal, enter

    async def fake_capture_pane(session: str, lines: int = 200) -> str:
        del session, lines
        return "Model is now thisisnotarealmodel"

    async def fake_sleep(seconds: float) -> None:
        del seconds

    monkeypatch.setattr("murder.tmux.send_keys", fake_send_keys)
    monkeypatch.setattr("murder.tmux.capture_pane", fake_capture_pane)
    monkeypatch.setattr("murder.harnesses.base.asyncio.sleep", fake_sleep)

    result = await adapter.attach("sess", Path("/repo")).probe_invalid_model(
        "thisisnotarealmodel"
    )

    assert not result.ok, "rejection detection issue: invalid model was accepted"
    assert "did not reject invalid model selection" in (result.message or "")
