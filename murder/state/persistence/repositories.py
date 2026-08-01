"""Repository registry and partition lifecycle operations."""

from __future__ import annotations

from pathlib import Path

from murder.state.persistence.connection import Connection
from murder.state.persistence.legacy_merge import COPY_ORDER
from murder.state.storage.paths import repository_id_path


def registered_repository_id(conn: Connection, repo_root: Path) -> str | None:
    try:
        candidate = repository_id_path(repo_root).read_text(encoding="utf-8").strip()
    except OSError:
        candidate = ""
    if candidate:
        row = conn.execute(
            "SELECT repository_id FROM repositories WHERE repository_id = ?", (candidate,)
        ).fetchone()
        if row is not None:
            return str(row["repository_id"])
    row = conn.execute(
        "SELECT repository_id FROM repositories WHERE root_path = ? ",
        (str(repo_root.resolve(strict=False)),),
    ).fetchone()
    return None if row is None else str(row["repository_id"])


def forget_repository(conn: Connection, repository_id: str) -> bool:
    """Atomically delete one repository partition from every shared table."""
    exists = conn.execute(
        "SELECT 1 FROM repositories WHERE repository_id = ?", (repository_id,)
    ).fetchone()
    if exists is None:
        return False
    table_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = {str(row[0]) for row in table_rows}
    ordered = [table for table in reversed(COPY_ORDER) if table in tables]
    ordered.extend(sorted(tables.difference(ordered).difference({"repositories"})))
    conn.execute("PRAGMA defer_foreign_keys = ON")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table in ordered:
            columns = {
                str(row[1])
                for row in conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            }
            if "repository_id" in columns:
                conn.execute(
                    f'DELETE FROM "{table}" WHERE repository_id = ?', (repository_id,)
                )
        conn.execute("DELETE FROM repositories WHERE repository_id = ?", (repository_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return True
