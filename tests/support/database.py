"""Shared-database test construction helpers.

Operational tests use :class:`RepoDb`, just like production.  The raw
connection is intentionally exposed only for schema assertions and for tests
that construct a legacy schema before running the migration chain.
"""

from __future__ import annotations

from pathlib import Path

import turso

from murder.state.persistence.connection import RepoDb
from murder.state.persistence.schema import init_db

TEST_REPOSITORY_ID = "00000000-0000-4000-8000-000000000001"
SECOND_TEST_REPOSITORY_ID = "00000000-0000-4000-8000-000000000002"


def open_test_repo_db(
    path: Path,
    *,
    repository_id: str = TEST_REPOSITORY_ID,
    initialize: bool = True,
) -> RepoDb:
    """Open a local Turso database and initialize the supplied partition."""
    conn = turso.connect(str(path), isolation_level=None)
    conn.row_factory = turso.Row
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA foreign_keys = ON;
        PRAGMA busy_timeout = 5000;
        """
    )
    if initialize:
        init_db(conn, repository_id=repository_id)
    return RepoDb(conn=conn, repository_id=repository_id)
