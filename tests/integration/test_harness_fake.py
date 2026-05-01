"""HarnessAdapter contract tests against a fake CLI.

Note: per mean opus #10 + D18, the fake CLI cannot exercise the
*real* cursor/CC pane regexes. These tests cover the IO/lifecycle
glue (startup → ready signal → send_prompt → capture). Real-pane
regex calibration is a manual smoke task in M1 dogfood.
"""

from __future__ import annotations

import pytest


@pytest.mark.integration
async def test_fake_harness_startup_to_ready(fake_harness_bin, tmux_session) -> None:
    # TODO(M1): launch fake_harness_bin in session; poll capture until 'add a follow-up' visible.
    pytest.skip("M1 stub")


@pytest.mark.integration
async def test_send_prompt_then_check_protocol(fake_harness_bin, tmux_session) -> None:
    # TODO(M1): send a prompt that triggers the fake to print '>>> CHECK: hello';
    # capture; assert detect_checks returns ['hello'].
    pytest.skip("M1 stub")
