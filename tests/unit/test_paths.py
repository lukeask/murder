"""Path conventions are stable; flat tickets dir per D9."""

from __future__ import annotations

from pathlib import Path

from murder.storage import paths


def test_ticket_md_is_flat() -> None:
    p = paths.ticket_md(Path("/repo"), "t007")
    assert p == Path("/repo/.agents/tickets/t007.md")
    assert "wave" not in str(p)


def test_db_path_under_agents() -> None:
    assert paths.db_path(Path("/x")) == Path("/x/.agents/murder.db")


def test_lock_path_under_agents() -> None:
    assert paths.lock_path(Path("/x")) == Path("/x/.agents/.lock")
