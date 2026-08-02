"""Repository-scoped access to the shared local Turso database."""

from __future__ import annotations

import fcntl
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

import turso

from murder.state.storage.paths import db_path, repository_id_path

DB_ERRORS = (sqlite3.Error, turso.Error)
DB_INTEGRITY_ERRORS = (sqlite3.IntegrityError, turso.IntegrityError)
DB_OPERATIONAL_ERRORS = (sqlite3.OperationalError, turso.OperationalError)


@contextmanager
def database_schema_lock(conn: Connection):  # type: ignore[no-untyped-def]
    """Serialize shared-database initialization across every repo process."""
    row = conn.execute("PRAGMA database_list").fetchone()
    database_file = "" if row is None else str(row[2] or "")
    if not database_file:
        yield
        return
    lock_path = Path(database_file + ".schema.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


class Connection(Protocol):
    """The sqlite-compatible subset used by murder's persistence layer."""

    row_factory: Any

    @property
    def in_transaction(self) -> bool: ...

    @property
    def isolation_level(self) -> str | None: ...

    def execute(self, sql: str, parameters: Any = ...) -> Any: ...
    def executemany(self, sql: str, parameters: Any) -> Any: ...
    def executescript(self, sql_script: str) -> Any: ...
    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class RepoDb:
    """A database connection that cannot lose its repository partition."""

    conn: Connection
    repository_id: str

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> RepoDb:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()


def execute_script(conn: Connection, script: str) -> None:
    """Execute a SQLite script without pyturso's trigger-offset parser bug."""
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            if statement.strip():
                conn.execute(statement)
            statement = ""
    if statement.strip():
        conn.execute(statement)


def connect(path: Path | None = None) -> Connection:
    """Open the consolidated database with the local, in-process Turso engine."""
    target = path or db_path()
    if str(target) != ":memory:":
        target.parent.mkdir(parents=True, exist_ok=True)
        from murder.state.persistence.backup import backup_before_driver_swap  # noqa: PLC0415

        backup_before_driver_swap(target)
    conn = turso.connect(str(target), isolation_level=None)
    conn.row_factory = turso.Row
    conn.executescript(
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;
        PRAGMA foreign_keys = ON;
        PRAGMA busy_timeout = 5000;
        """
    )
    return conn


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_repo_id(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
        return str(UUID(value))
    except (OSError, ValueError):
        return None


def write_repository_id(path: Path, repository_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(repository_id + "\n", encoding="utf-8")
    temporary.replace(path)


def resolve_repository(conn: Connection, repo_root: Path) -> str:
    """Resolve and persist stable identity for one checkout.

    The registry is authoritative. A valid checkout-local id survives moves.
    Root-path lookup preserves identities whose local id file has been lost.
    """
    root = str(repo_root.resolve(strict=False))
    id_file = repository_id_path(repo_root)
    repository_id = _read_repo_id(id_file)
    now = _utc_now()

    if repository_id is not None:
        row = conn.execute(
            "SELECT repository_id FROM repositories WHERE repository_id = ?",
            (repository_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO repositories "
                "(repository_id, root_path, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
                (repository_id, root, now, now),
            )
        else:
            conn.execute(
                "UPDATE repositories SET root_path = ?, last_seen_at = ? WHERE repository_id = ?",
                (root, now, repository_id),
            )
        return repository_id

    row = conn.execute(
        "SELECT repository_id FROM repositories WHERE root_path = ? "
        "ORDER BY last_seen_at DESC LIMIT 1",
        (root,),
    ).fetchone()
    repository_id = str(row["repository_id"]) if row is not None else str(uuid4())
    if row is None:
        conn.execute(
            "INSERT INTO repositories "
            "(repository_id, root_path, created_at, last_seen_at) VALUES (?, ?, ?, ?)",
            (repository_id, root, now, now),
        )
    else:
        conn.execute(
            "UPDATE repositories SET last_seen_at = ? WHERE repository_id = ?",
            (now, repository_id),
        )
    write_repository_id(id_file, repository_id)
    return repository_id


def open_repo_db(repo_root: Path) -> RepoDb:
    """Open, initialize, and scope the shared database for ``repo_root``."""
    from murder.state.persistence.schema import init_db  # noqa: PLC0415

    conn = connect()
    init_db(conn)
    from murder.state.persistence.legacy_merge import (  # noqa: PLC0415
        merge_known_legacy_databases,
    )

    # Schema migration holds this lock independently.  Retake it for the rest
    # of first-run setup so another process cannot observe and import the same
    # legacy file before this process has committed its repository identity and
    # renamed the source database.
    with database_schema_lock(conn):
        # See init_db: a connection that waited for another initializer may
        # retain an old read snapshot until it rolls back.
        conn.rollback()
        merge_known_legacy_databases(conn, repo_root)
        repository_id = resolve_repository(conn, repo_root)
    db = RepoDb(conn=conn, repository_id=repository_id)
    from murder.state.persistence.notetaker import (  # noqa: PLC0415
        ensure_notetaker_context_row,
    )

    ensure_notetaker_context_row(db)
    return db
