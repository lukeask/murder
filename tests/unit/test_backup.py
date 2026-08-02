"""Tests for failure-safe local database backups."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from murder.state.persistence import backup


def test_failed_backup_closes_connections_removes_partial_output_and_can_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    source = tmp_path / "source.db"
    source.touch()
    destination = tmp_path / "backup.db"
    source_connection = MagicMock()
    destination_connection = MagicMock()
    source_connection.backup.side_effect = sqlite3.OperationalError("disk full")

    def fake_connect(path: str) -> MagicMock:
        if path == str(source):
            return source_connection
        destination.touch()
        return destination_connection

    monkeypatch.setattr(sqlite3, "connect", fake_connect)

    with pytest.raises(sqlite3.OperationalError, match="disk full"):
        backup.backup_database(source, destination)

    assert source_connection.close.call_count == 1
    assert destination_connection.close.call_count == 1
    assert not destination.exists()

    source_connection.backup.side_effect = None
    assert backup.backup_database(source, destination) == destination
