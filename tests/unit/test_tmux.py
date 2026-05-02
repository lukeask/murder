"""Unit tests for tmux command helpers."""

from __future__ import annotations

import pytest

from murder import tmux


@pytest.mark.asyncio
async def test_send_keys_large_payload_settles_before_enter(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, ...]] = []
    sleeps: list[float] = []

    async def fake_tmux(*args: str, check: bool = True) -> tuple[int, str, str]:
        calls.append(args)
        return 0, "", ""

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(tmux, "_tmux", fake_tmux)
    monkeypatch.setattr(tmux.asyncio, "sleep", fake_sleep)

    await tmux.send_keys("sess", "x" * (tmux.LARGE_PAYLOAD_BYTES + 1))

    assert calls[0][:3] == ("load-buffer", "-b", calls[0][2])
    assert calls[1] == ("paste-buffer", "-d", "-t", "sess", "-b", calls[0][2])
    assert calls[2] == ("send-keys", "-t", "sess", "Enter")
    assert sleeps == [tmux.PASTE_ENTER_DELAY_S]
