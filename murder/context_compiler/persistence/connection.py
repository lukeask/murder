"""Context-index connection lifecycle (independent of murder.db).

Exposes explicit open/close rather than a process-global singleton. Callers
own the connection and pass it into persistence functions.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from murder.context_compiler.persistence.schema import SCHEMA_SQL, SCHEMA_VERSION
from murder.state.storage.paths import context_index_db_path, murder_dir


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def open_context_index(
    repo_root: Path,
    *,
    db_path: Path | None = None,
) -> sqlite3.Connection:
    """Open ``.murder/context-index.db``, enabling WAL + foreign keys.

    Creates ``.murder/`` if needed. Initializes the schema idempotently.
    Does not attach or otherwise touch ``murder.db``.
    """
    murder_dir(repo_root).mkdir(parents=True, exist_ok=True)
    path = db_path if db_path is not None else context_index_db_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        isolation_level=None,
        check_same_thread=False,
        timeout=10.0,
    )
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA foreign_keys = ON;
        PRAGMA busy_timeout = 5000;
        """
    )
    init_context_index_schema(conn)
    return conn


def init_context_index_schema(conn: sqlite3.Connection) -> None:
    """Apply DDL idempotently and ensure the schema-version singleton row.

    Experimental index: an incompatible ``SCHEMA_VERSION`` recreates tables
    rather than running a migration framework. Never touches ``murder.db``.
    """
    row = conn.execute(
        """
        SELECT name FROM sqlite_master
         WHERE type = 'table' AND name = 'context_index_schema'
        """
    ).fetchone()
    if row is not None:
        version_row = conn.execute(
            "SELECT schema_version FROM context_index_schema WHERE singleton = 1"
        ).fetchone()
        if version_row is not None and int(version_row["schema_version"]) != SCHEMA_VERSION:
            # Incompatible experimental schema — wipe and rebuild.
            tables = conn.execute(
                """
                SELECT name FROM sqlite_master
                 WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            ).fetchall()
            conn.execute("PRAGMA foreign_keys = OFF")
            for table in tables:
                conn.execute(f'DROP TABLE IF EXISTS "{table["name"]}"')
            conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript(SCHEMA_SQL)
    row = conn.execute(
        "SELECT schema_version FROM context_index_schema WHERE singleton = 1"
    ).fetchone()
    if row is None:
        conn.execute(
            """
            INSERT INTO context_index_schema (singleton, schema_version, updated_at)
            VALUES (1, ?, ?)
            """,
            (SCHEMA_VERSION, _now()),
        )
    elif int(row["schema_version"]) != SCHEMA_VERSION:
        conn.execute(
            """
            UPDATE context_index_schema
               SET schema_version = ?, updated_at = ?
             WHERE singleton = 1
            """,
            (SCHEMA_VERSION, _now()),
        )


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Explicit BEGIN/COMMIT/ROLLBACK for multi-statement atomicity.

    The connection uses ``isolation_level=None`` (autocommit), so callers that
    need a real transaction must wrap writes with this helper.
    """
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


__all__ = [
    "init_context_index_schema",
    "open_context_index",
    "transaction",
]
