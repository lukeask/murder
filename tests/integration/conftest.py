"""Integration fixtures — these need a live tmux server.

Skipped automatically when tmux is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import Iterator

import pytest


@pytest.fixture(scope="session", autouse=True)
def _require_tmux() -> None:
    if shutil.which("tmux") is None:
        pytest.skip("tmux not available", allow_module_level=True)


@pytest.fixture
def tmux_session(tmp_path) -> Iterator[str]:
    """Yield a unique tmux session name; tear it down after the test."""
    name = f"murder_test_{uuid.uuid4().hex[:8]}"
    yield name
    # TODO(M0): subprocess.run(['tmux','kill-session','-t',name], check=False).
    subprocess.run(["tmux", "kill-session", "-t", name], check=False)


@pytest.fixture
def fake_harness_bin(tmp_path):
    """A Python script that mimics a coding-CLI harness for adapter tests.

    Prints a known prompt, echoes input, supports the D6 protocol so we
    can exercise Augur's parsing without burning real tokens.
    """
    # TODO(M1): write a small Python script (~30 lines) into tmp_path/fake_harness;
    # print 'add a follow-up>' on startup, read input, echo back, support
    # '>>> CHECK:' / '>>> ASK:' / '>>> DONE'.
    pytest.skip("M1: fake_harness_bin not yet implemented")
