"""Unit tests for report.create ensure + conflict guard."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from murder.state.persistence.schema import init_db
from murder.work.reports import ensure_report


@pytest.fixture()
def conn_and_root(tmp_path: Path) -> tuple[sqlite3.Connection, Path]:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn, tmp_path


def test_ensure_report_creates_file_and_row(conn_and_root: tuple[sqlite3.Connection, Path]) -> None:
    conn, root = conn_and_root
    row = ensure_report(conn, root, "hello", body="# hi\n")
    assert row["name"] == "hello"
    path = root / ".murder" / "reports" / "hello.md"
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "# hi\n"


def test_ensure_report_does_not_clobber_existing(
    conn_and_root: tuple[sqlite3.Connection, Path],
) -> None:
    conn, root = conn_and_root
    ensure_report(conn, root, "keep", body="original\n")
    again = ensure_report(conn, root, "keep", body="new\n")
    assert again["body"] == "original\n"
    path = root / ".murder" / "reports" / "keep.md"
    assert path.read_text(encoding="utf-8") == "original\n"


def test_ensure_report_rejects_unsafe_name(
    conn_and_root: tuple[sqlite3.Connection, Path],
) -> None:
    conn, root = conn_and_root
    with pytest.raises(ValueError, match="safe path"):
        ensure_report(conn, root, "../escape")
