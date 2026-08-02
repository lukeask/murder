"""Consistent local backups for the consolidated murder database."""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from murder.state.storage.paths import db_path
from murder.user_config import config_dir


def default_backup_path(*, label: str = "backup") -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    return config_dir() / "backups" / f"murder-{stamp}-{label}.db"


def backup_database(source: Path | None = None, out: Path | None = None) -> Path:
    """Create a transactionally consistent SQLite-format backup.

    Turso uses the SQLite file format, so stdlib's online backup API is a safe
    copy mechanism even while another local process is writing in WAL mode.
    """
    source = source or db_path()
    if not source.exists():
        raise FileNotFoundError(source)
    out = out or default_backup_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise FileExistsError(out)
    src: sqlite3.Connection | None = None
    dst: sqlite3.Connection | None = None
    completed = False
    try:
        src = sqlite3.connect(str(source))
        dst = sqlite3.connect(str(out))
        src.backup(dst)
        dst.close()
        dst = None
        shutil.copystat(source, out)
        completed = True
    finally:
        # Connection context managers commit or roll back, but do not close.
        # Close before deleting a failed destination so retry works on every
        # platform, including those that disallow unlinking open files.
        try:
            if dst is not None:
                dst.close()
        finally:
            try:
                if src is not None:
                    src.close()
            finally:
                if not completed:
                    # ``backup`` may have created a partial SQLite file before
                    # failing. It did not exist before this call, so removing
                    # it makes retrying this exact destination safe.
                    out.unlink(missing_ok=True)
    return out


def backup_before_driver_swap(source: Path) -> Path | None:
    """Back up an existing shared DB once before pyturso first opens it."""
    if not source.exists() or source.stat().st_size == 0:
        return None
    marker = source.parent / ".pyturso-0.7.2-backup"
    if marker.exists():
        return None
    backup = backup_database(source, default_backup_path(label="pre-pyturso"))
    marker.write_text(str(backup) + "\n", encoding="utf-8")
    return backup
