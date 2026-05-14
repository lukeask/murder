"""DB-backed planning notes — the "notetaker" scratchpad docs.

Notes are dated markdown documents (`.murder/notes/<YYYY-MM-DD>.md`).
Runtime now maintains a DB+file mirror and records note revisions in
`note_revisions` for safety/auditability.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from murder import db as dbmod
from murder.storage.filesystem import atomic_write_text
from murder.storage.paths import note_md


def today_name() -> str:
    """The note name for today (`YYYY-MM-DD`, UTC — matches db._now)."""
    return datetime.utcnow().date().isoformat()


def _rel_path(repo_root: Path, name: str) -> str:
    return str(note_md(repo_root, name).relative_to(repo_root))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_revision(
    conn: sqlite3.Connection, name: str, body: str, *, source: str
) -> None:
    dbmod.insert_note_revision(
        conn,
        name,
        source=source,
        body=body,
        content_hash=content_hash(body),
    )


def ensure_note(conn: sqlite3.Connection, repo_root: Path, name: str) -> dict[str, Any]:
    """Return note row for `name`, importing existing files without clobbering."""
    row = dbmod.get_note(conn, name)
    rel = _rel_path(repo_root, name)
    path = repo_root / rel
    if row is not None:
        if not path.exists():
            atomic_write_text(path, str(row["body"]))
        return row
    if path.exists():
        body = path.read_text(encoding="utf-8")
        dbmod.upsert_note(conn, name, body=body, materialized_path=rel)
        _record_revision(conn, name, body, source="bootstrap")
    else:
        body = ""
        dbmod.upsert_note(conn, name, body=body, materialized_path=rel)
        atomic_write_text(path, body)
        _record_revision(conn, name, body, source="bootstrap")
    return dbmod.get_note(conn, name) or {"name": name, "body": body, "materialized_path": rel}


def read_note(conn: sqlite3.Connection, name: str) -> str:
    row = dbmod.get_note(conn, name)
    return str(row["body"]) if row else ""


def write_note(
    conn: sqlite3.Connection,
    repo_root: Path,
    name: str,
    body: str,
    *,
    source: str = "agent",
) -> None:
    """Replace the body of note `name` in the DB and re-materialize its file."""
    existing = dbmod.get_note(conn, name)
    old_body = str(existing["body"]) if existing is not None else None
    rel = _rel_path(repo_root, name)
    dbmod.upsert_note(conn, name, body=body, materialized_path=rel)
    atomic_write_text(repo_root / rel, body)
    if old_body != body:
        _record_revision(conn, name, body, source=source)


def latest_prior_note(
    conn: sqlite3.Connection, exclude: str
) -> tuple[str, str] | None:
    """The most recently-named non-empty note other than `exclude`, as (name, body)."""
    for row in dbmod.list_notes(conn):
        if row["name"] != exclude and row["size"]:
            full = dbmod.get_note(conn, row["name"])
            if full:
                return row["name"], str(full["body"])
    return None
