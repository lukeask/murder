from __future__ import annotations

import sqlite3

from murder.state.persistence.schema import init_db
from murder.state.persistence.usage import (
    clear_usage_probe_session_id,
    get_usage_probe_session_id,
    set_usage_probe_session_id,
)


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def test_usage_probe_session_cache_round_trips_and_clears() -> None:
    conn = _db()

    assert get_usage_probe_session_id(conn, "codex") is None

    set_usage_probe_session_id(conn, "codex", "session-1")
    assert get_usage_probe_session_id(conn, "codex") == "session-1"

    set_usage_probe_session_id(conn, "codex", "session-2")
    assert get_usage_probe_session_id(conn, "codex") == "session-2"

    clear_usage_probe_session_id(conn, "codex")
    assert get_usage_probe_session_id(conn, "codex") is None
