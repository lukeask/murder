"""Top-level pytest config.

Project-wide fixtures live in subdirectories' conftests so unit tests
don't accidentally inherit tmux/IO concerns.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session")
def smoke_enabled() -> bool:
    """True iff `MURDER_MANUAL_SMOKE=1` is set."""
    import os

    return os.environ.get("MURDER_MANUAL_SMOKE") == "1"
