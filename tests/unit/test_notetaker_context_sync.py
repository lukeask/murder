"""`NotetakerContextSync` materializes and imports `.murder/notetakercontext.md`."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from murder import db as dbmod
from murder.db import connect, init_schema
from murder.notetaker_context_sync import NotetakerContextSync
from murder.storage.paths import db_path, notetaker_context_md


@pytest.fixture
def repo_with_db(tmp_path: Path) -> tuple[Path, sqlite3.Connection]:
    root = tmp_path / "repo"
    root.mkdir()
    conn = connect(db_path(root))
    init_schema(conn)
    return root, conn


def test_reconcile_all_writes_empty_context_file(
    repo_with_db: tuple[Path, sqlite3.Connection],
) -> None:
    root, conn = repo_with_db
    sync = NotetakerContextSync(root, conn)

    async def _run() -> None:
        await sync.reconcile_all()

    asyncio.run(_run())
    path = notetaker_context_md(root)
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == ""
    row = dbmod.get_notetaker_context(conn)
    assert row is not None
    assert len(str(row["body"])) == 0


def test_file_edit_updates_database(
    repo_with_db: tuple[Path, sqlite3.Connection],
) -> None:
    root, conn = repo_with_db
    sync = NotetakerContextSync(root, conn)

    async def _run() -> None:
        await sync.reconcile_all()

    asyncio.run(_run())
    path = notetaker_context_md(root)
    path.write_text("synced from disk\n", encoding="utf-8")

    async def _push() -> None:
        await sync.reconcile_file(path)

    asyncio.run(_push())
    row = dbmod.get_notetaker_context(conn)
    assert row is not None
    assert row["body"] == "synced from disk\n"
