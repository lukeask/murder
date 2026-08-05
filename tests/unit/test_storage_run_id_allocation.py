"""Run ID allocation — repo prefix + collision suffix contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from murder.state.persistence.runs import insert_run
from murder.state.storage.run_id_allocation import allocate_run_id
from tests.support.database import (
    SECOND_TEST_REPOSITORY_ID,
    TEST_REPOSITORY_ID,
    open_test_repo_db,
)


def test_allocate_run_id_appends_suffix_when_timestamp_collides(
    repo_root,
    monkeypatch,
) -> None:
    monkeypatch.setattr("murder.state.storage.run_id_allocation.time.time", lambda: 1_717_171_717)

    first = allocate_run_id(repo_root, repository_id=TEST_REPOSITORY_ID)
    second = allocate_run_id(repo_root, repository_id=TEST_REPOSITORY_ID)

    assert first == f"{TEST_REPOSITORY_ID}-1717171717"
    assert second == f"{TEST_REPOSITORY_ID}-1717171717_1"


def test_allocate_run_id_prefixes_repository_id_so_hosts_do_not_collide(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Two repos activating in the same second must not share a runs PK."""
    monkeypatch.setattr("murder.state.storage.run_id_allocation.time.time", lambda: 1_717_171_717)

    root_a = tmp_path / "repo-a"
    root_b = tmp_path / "repo-b"
    root_a.mkdir()
    root_b.mkdir()

    run_a = allocate_run_id(root_a, repository_id=TEST_REPOSITORY_ID)
    run_b = allocate_run_id(root_b, repository_id=SECOND_TEST_REPOSITORY_ID)

    assert run_a == f"{TEST_REPOSITORY_ID}-1717171717"
    assert run_b == f"{SECOND_TEST_REPOSITORY_ID}-1717171717"
    assert run_a != run_b

    db_path = tmp_path / "murder.db"
    first = open_test_repo_db(db_path)
    second = open_test_repo_db(db_path, repository_id=SECOND_TEST_REPOSITORY_ID)
    try:
        insert_run(first, run_a, "{}")
        insert_run(second, run_b, "{}")
        rows = first.conn.execute("SELECT repository_id, run_id FROM runs ORDER BY run_id").fetchall()
        assert {(str(r["repository_id"]), str(r["run_id"])) for r in rows} == {
            (TEST_REPOSITORY_ID, run_a),
            (SECOND_TEST_REPOSITORY_ID, run_b),
        }
    finally:
        first.close()
        second.close()


def test_allocate_run_id_rejects_empty_repository_id(repo_root) -> None:
    with pytest.raises(ValueError, match="repository_id"):
        allocate_run_id(repo_root, repository_id="")
