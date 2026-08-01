"""DB-backed report helpers — thin twin of notes ensure/write."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from murder.state.persistence import reports as reports_db
from murder.state.persistence.connection import RepoDb
from murder.state.storage.filesystem import atomic_write_text
from murder.state.storage.paths import report_md, reports_dir


def _rel_path(repo_root: Path, name: str) -> str:
    return str(report_md(repo_root, name).relative_to(repo_root))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_revision(db: RepoDb, name: str, body: str, *, source: str) -> None:
    reports_db.insert_report_revision(
        db,
        name,
        source=source,
        body=body,
        content_hash=content_hash(body),
    )


def ensure_report(
    db: RepoDb,
    repo_root: Path,
    name: str,
    *,
    body: str = "",
) -> dict[str, Any]:
    """Return report row for ``name``, creating an empty (or seeded) file when missing.

    Does not clobber an existing report's body when the file or DB row already exists.
    """
    safe = name.strip()
    if not safe or Path(safe).name != safe or "\\" in safe or safe in {".", ".."}:
        raise ValueError("report name must be a single safe path component")
    reports_dir(repo_root).mkdir(parents=True, exist_ok=True)
    row = reports_db.get_report(db, safe)
    rel = _rel_path(repo_root, safe)
    path = repo_root / rel
    if row is not None:
        if not path.exists():
            atomic_write_text(path, str(row["body"]))
        return row
    if path.exists():
        existing_body = path.read_text(encoding="utf-8")
        reports_db.upsert_report(db, safe, body=existing_body, materialized_path=rel)
        _record_revision(db, safe, existing_body, source="bootstrap")
    else:
        text = body if body.endswith("\n") or body == "" else body + "\n"
        reports_db.upsert_report(db, safe, body=text, materialized_path=rel)
        atomic_write_text(path, text)
        _record_revision(db, safe, text, source="bootstrap")
    return reports_db.get_report(db, safe) or {
        "name": safe,
        "body": body,
        "materialized_path": rel,
    }


__all__ = ["content_hash", "ensure_report"]
