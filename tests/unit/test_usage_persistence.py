from __future__ import annotations

from pathlib import Path

from murder.state.persistence.connection import RepoDb
from murder.state.persistence.usage import (
    clear_usage_probe_session_id,
    get_usage_probe_session_id,
    set_usage_probe_session_id,
)
from tests.support.database import open_test_repo_db


def _db() -> RepoDb:
    return open_test_repo_db(Path(":memory:"))


def test_usage_probe_session_cache_round_trips_and_clears() -> None:
    db = _db()
    try:
        assert get_usage_probe_session_id(db, "codex") is None

        set_usage_probe_session_id(db, "codex", "session-1")
        assert get_usage_probe_session_id(db, "codex") == "session-1"

        set_usage_probe_session_id(db, "codex", "session-2")
        assert get_usage_probe_session_id(db, "codex") == "session-2"

        clear_usage_probe_session_id(db, "codex")
        assert get_usage_probe_session_id(db, "codex") is None
    finally:
        db.close()
