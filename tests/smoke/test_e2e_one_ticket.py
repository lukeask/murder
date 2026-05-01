"""Manual smoke: real cursor + real OpenRouter against a one-ticket toy repo.

Gated behind `MURDER_MANUAL_SMOKE=1`. Requires:
- `agent` (cursor CLI) on PATH
- `OPENROUTER_API_KEY` set
- a tmux server

This is the M1 dogfood entry point.
"""

from __future__ import annotations

import pytest


@pytest.mark.smoke
async def test_kickoff_one_ticket_runs_cursor_to_done(smoke_enabled: bool, tmp_path) -> None:
    if not smoke_enabled:
        pytest.skip("MURDER_MANUAL_SMOKE not set")
    # TODO(M1):
    # 1. set up tmp_path as a tiny git repo with one source file
    # 2. murder init; write one ticket via DB helpers (no Collaborator yet)
    # 3. orchestrator.kickoff_ready()
    # 4. poll until ticket status == done OR timeout
    # 5. assert >>> DONE was emitted; assert git diff inside write_set
    pytest.skip("M1 stub")
