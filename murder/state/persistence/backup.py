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
    with sqlite3.connect(str(source)) as src, sqlite3.connect(str(out)) as dst:
        src.backup(dst)
    shutil.copystat(source, out)
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
