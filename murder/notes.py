"""DB-backed planning notes — the "notetaker" scratchpad docs.

Notes are dated markdown documents (`.murder/notes/<YYYY-MM-DD>.md`). The
SQLite `notes` table is authoritative; every write also materializes the
markdown file so the doc stays browsable/editable on disk. Importing on-disk
edits back into the DB is intentionally left for a future bidirectional-sync
module — the notetaker agent is currently the only writer.
"""

from __future__ import annotations

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


def ensure_note(conn: sqlite3.Connection, repo_root: Path, name: str) -> dict[str, Any]:
    """Return the note row for `name`, creating an empty one (DB + file) if absent."""
    row = dbmod.get_note(conn, name)
    if row is not None:
        return row
    rel = _rel_path(repo_root, name)
    dbmod.upsert_note(conn, name, body="", materialized_path=rel)
    atomic_write_text(repo_root / rel, "")
    return dbmod.get_note(conn, name) or {
        "name": name, "body": "", "materialized_path": rel,
    }


def read_note(conn: sqlite3.Connection, name: str) -> str:
    row = dbmod.get_note(conn, name)
    return str(row["body"]) if row else ""


def write_note(conn: sqlite3.Connection, repo_root: Path, name: str, body: str) -> None:
    """Replace the body of note `name` in the DB and re-materialize its file."""
    rel = _rel_path(repo_root, name)
    dbmod.upsert_note(conn, name, body=body, materialized_path=rel)
    atomic_write_text(repo_root / rel, body)


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
