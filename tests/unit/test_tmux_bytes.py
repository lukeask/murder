"""Byte-transport guarantees for interactive tmux input."""

from __future__ import annotations

import pytest

from murder.runtime.terminal import tmux


@pytest.mark.asyncio
async def test_send_bytes_uses_hex_keycodes_without_text_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def run(*args: str, **_kwargs: object) -> tuple[int, str, str]:
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(tmux, "_tmux", run)
    await tmux.send_bytes("editor", b"\x1b\x17\xc3\xa9\x00\x1b[200~paste\x1b[201~")

    assert calls == [
        (
            "send-keys",
            "-t",
            "editor",
            "-H",
            "1b",
            "17",
            "c3",
            "a9",
            "00",
            "1b",
            "5b",
            "32",
            "30",
            "30",
            "7e",
            "70",
            "61",
            "73",
            "74",
            "65",
            "1b",
            "5b",
            "32",
            "30",
            "31",
            "7e",
        )
    ]


@pytest.mark.asyncio
async def test_send_bytes_chunks_large_payload_in_exact_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    async def run(*args: str, **_kwargs: object) -> tuple[int, str, str]:
        calls.append(args)
        return 0, "", ""

    monkeypatch.setattr(tmux, "_tmux", run)
    payload = bytes(range(256)) + bytes(range(256)) + b"tail"
    await tmux.send_bytes("editor", payload)

    reconstructed = bytes(
        int(value, 16)
        for call in calls
        for value in call[4:]
    )
    assert [len(call) - 4 for call in calls] == [512, 4]
    assert reconstructed == payload
