"""Path conventions are stable; flat tickets dir per D9."""

from __future__ import annotations

from pathlib import Path

from murder.storage import paths


def test_ticket_md_is_flat() -> None:
    p = paths.ticket_md(Path("/repo"), "t007")
    assert p == Path("/repo/.murder/tickets/t007.md")
    assert "wave" not in str(p)


def test_db_path_under_agents() -> None:
    assert paths.db_path(Path("/x")) == Path("/x/.murder/murder.db")


def test_lock_path_under_agents() -> None:
    assert paths.lock_path(Path("/x")) == Path("/x/.murder/.lock")


def test_logs_dir_under_agents() -> None:
    assert paths.logs_dir(Path("/repo")) == Path("/repo/.murder/logs")
