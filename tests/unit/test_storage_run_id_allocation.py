"""Run ID allocation — collision suffix is the only non-obvious contract here."""

from __future__ import annotations

from murder.storage.run_id_allocation import allocate_run_id


def test_allocate_run_id_appends_suffix_when_timestamp_collides(
    repo_root,
    monkeypatch,
) -> None:
    monkeypatch.setattr("murder.storage.run_id_allocation.time.time", lambda: 1_717_171_717)

    first = allocate_run_id(repo_root)
    second = allocate_run_id(repo_root)

    assert first == "1717171717"
    assert second == "1717171717_1"
