"""tmux helper functions exercise a real tmux server."""

from __future__ import annotations

import pytest


@pytest.mark.integration
async def test_create_capture_kill_roundtrip(tmux_session: str, tmp_path) -> None:
    # TODO(M0): tmux.create_session(name, cwd=tmp_path); tmux.capture_pane → has shell prompt;
    # tmux.kill_session; tmux.session_exists → False.
    pytest.skip("M0 stub")


@pytest.mark.integration
async def test_send_keys_small_payload(tmux_session: str, tmp_path) -> None:
    # TODO(M0): create session; send_keys 'echo hi'; capture_pane → 'hi'.
    pytest.skip("M0 stub")


@pytest.mark.integration
async def test_send_keys_large_payload_uses_load_buffer(tmux_session: str, tmp_path) -> None:
    """D10: payloads > LARGE_PAYLOAD_BYTES go via load-buffer/paste-buffer."""
    # TODO(M0): send a 5KB string; capture; assert all bytes present.
    pytest.skip("M0 stub")
