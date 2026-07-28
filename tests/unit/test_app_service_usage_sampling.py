"""In-process usage sampling must invalidate schedule for websocket TUI refresh."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from murder.app.service import usage_sampling as usage_sampling_service
from murder.state.persistence.schema import get_db, init_db


@pytest.mark.asyncio
async def test_sample_usage_invalidates_schedule_when_snapshots_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = get_db(tmp_path / "state.db")
    init_db(db)

    monkeypatch.setattr(
        usage_sampling_service,
        "Config",
        SimpleNamespace(load=lambda _root: object()),
    )
    monkeypatch.setattr(
        usage_sampling_service,
        "UsageSamplingContext",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        usage_sampling_service,
        "harness_kinds_to_sample",
        lambda _ctx, modes=None: ["codex", "cursor"],
    )

    async def _sample(_ctx, *, modes=None):  # noqa: ANN001
        del modes
        return (2, 0)

    monkeypatch.setattr(usage_sampling_service, "sample_harness_usages", _sample)

    result = await usage_sampling_service.sample_usage(
        repo_root=tmp_path, db=db, modes={"http"}
    )

    assert result["stored"] == 2
    subjects = [
        row["subject_key"]
        for row in db.execute(
            "SELECT subject_key FROM projection_inputs "
            "WHERE projection = 'schedule' ORDER BY sequence"
        ).fetchall()
    ]
    assert subjects == ["usage:codex", "usage:cursor"]


@pytest.mark.asyncio
async def test_sample_usage_skips_invalidation_when_nothing_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = get_db(tmp_path / "state.db")
    init_db(db)

    monkeypatch.setattr(
        usage_sampling_service,
        "Config",
        SimpleNamespace(load=lambda _root: object()),
    )
    monkeypatch.setattr(
        usage_sampling_service,
        "UsageSamplingContext",
        lambda **kwargs: object(),
    )
    monkeypatch.setattr(
        usage_sampling_service,
        "harness_kinds_to_sample",
        lambda _ctx, modes=None: ["codex"],
    )

    async def _sample(_ctx, *, modes=None):  # noqa: ANN001
        del modes
        return (0, 1)

    monkeypatch.setattr(usage_sampling_service, "sample_harness_usages", _sample)

    await usage_sampling_service.sample_usage(repo_root=tmp_path, db=db)

    assert (
        db.execute("SELECT 1 FROM projection_inputs WHERE projection = 'schedule'").fetchone()
        is None
    )
