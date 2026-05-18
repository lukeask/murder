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
from murder.storage.paths import note_md, notes_dir


def today_name() -> str:
    """The note name for today (`YYYY-MM-DD`, UTC — matches db._now)."""
    return datetime.utcnow().date().isoformat()


def timestamp_name(now: datetime | None = None) -> str:
    """Filesystem-safe provisional name for immediate capture notes."""
    dt = now or datetime.utcnow()
    return dt.strftime("%Y%m%dT%H%M%S%fZ")


def retired_notes_dir(repo_root: Path) -> Path:
    return notes_dir(repo_root) / "retired_notes"


def _rel_path(repo_root: Path, name: str) -> str:
    return str(note_md(repo_root, name).relative_to(repo_root))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_revision(conn: sqlite3.Connection, name: str, body: str, *, source: str) -> None:
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


def create_timestamped_note(
    conn: sqlite3.Connection,
    repo_root: Path,
    body: str,
    *,
    source: str = "agent",
    now: datetime | None = None,
) -> str:
    """Create a durable provisional note file and DB mirror immediately."""
    base = timestamp_name(now)
    name = base
    i = 2
    while dbmod.get_note(conn, name) is not None or note_md(repo_root, name).exists():
        name = f"{base}-{i}"
        i += 1
    rel = _rel_path(repo_root, name)
    text = body.rstrip() + "\n"
    atomic_write_text(repo_root / rel, text)
    dbmod.upsert_note(conn, name, body=text, materialized_path=rel)
    _record_revision(conn, name, text, source=source)
    return name


def active_note_name_exists(
    conn: sqlite3.Connection,
    repo_root: Path,
    name: str,
    *,
    exclude: str | None = None,
) -> bool:
    if name == exclude:
        return False
    row = dbmod.get_note(conn, name)
    if row is not None and str(row.get("status", "active")) == "active":
        return True
    path = note_md(repo_root, name)
    return path.exists()


def rename_note(
    conn: sqlite3.Connection,
    repo_root: Path,
    old_name: str,
    new_name: str,
) -> str:
    """Rename an active note file and DB row, preserving the DB UUID identity."""
    if old_name == new_name:
        return old_name
    if active_note_name_exists(conn, repo_root, new_name, exclude=old_name):
        raise FileExistsError(f"note already exists: {new_name}")
    row = dbmod.get_note(conn, old_name)
    if row is None:
        raise FileNotFoundError(f"note not found: {old_name}")
    old_path = repo_root / str(row["materialized_path"])
    if not old_path.exists():
        old_path = note_md(repo_root, old_name)
    new_path = note_md(repo_root, new_name)
    new_path.parent.mkdir(parents=True, exist_ok=True)
    if old_path.exists():
        old_path.rename(new_path)
    else:
        atomic_write_text(new_path, str(row["body"]))
    dbmod.rename_note(
        conn,
        old_name,
        new_name,
        materialized_path=str(new_path.relative_to(repo_root)),
    )
    return new_name


def retire_note(conn: sqlite3.Connection, repo_root: Path, name: str) -> Path:
    """Move an active note out of the sidebar into `.murder/notes/retired_notes/`."""
    row = dbmod.get_note(conn, name)
    if row is None:
        raise FileNotFoundError(f"note not found: {name}")
    old_path = repo_root / str(row["materialized_path"])
    if not old_path.exists():
        old_path = note_md(repo_root, name)
    dest_dir = retired_notes_dir(repo_root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{name}.md"
    if dest.exists():
        base = dest_dir / name
        i = 2
        while True:
            candidate = base.with_name(f"{name}-{i}.md")
            if not candidate.exists():
                dest = candidate
                break
            i += 1
    if old_path.exists():
        old_path.rename(dest)
    else:
        atomic_write_text(dest, str(row["body"]))
    dbmod.mark_note_retired(
        conn,
        name,
        materialized_path=str(dest.relative_to(repo_root)),
    )
    return dest


def latest_prior_note(conn: sqlite3.Connection, exclude: str) -> tuple[str, str] | None:
    """The most recently-named non-empty note other than `exclude`, as (name, body)."""
    for row in dbmod.list_notes(conn):
        if row["name"] != exclude and row["size"]:
            full = dbmod.get_note(conn, row["name"])
            if full:
                return row["name"], str(full["body"])
    return None
