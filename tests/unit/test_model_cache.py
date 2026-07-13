"""Configured static model-catalog contracts.

No test in this module may start a harness or patch a terminal probe: live
verification belongs to a verified model-selection operation, not settings
catalog refresh.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from murder.llm.harnesses import REGISTRY
from murder.llm.harnesses.model_cache import (
    CATALOG_ADVISORY,
    configured_harnesses,
    get_available_models,
    refresh_and_persist_harness_models,
)
from murder.state.persistence.harness_models import get_all_harness_models
from murder.state.persistence.schema import init_db


def test_accessor_returns_configured_catalog_and_fresh_list() -> None:
    expected = list(REGISTRY["claude_code"].available_startup_models)
    models = get_available_models("claude_code")
    assert models == expected
    models.append(("mutated", "Mutated"))
    assert get_available_models("claude_code") == expected


def test_catalog_covers_all_registered_harnesses_without_capability_probe() -> None:
    assert configured_harnesses() == sorted(REGISTRY)
    assert get_available_models("not_a_harness") == []


def test_refresh_persists_static_catalog_with_explicit_advisory(tmp_path: Path) -> None:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    init_db(connection)

    asyncio.run(refresh_and_persist_harness_models(tmp_path, connection))

    rows = get_all_harness_models(connection)
    assert {row["harness"] for row in rows} == set(configured_harnesses())
    assert all(row["discovery_error"] == CATALOG_ADVISORY for row in rows)
    assert next(row for row in rows if row["harness"] == "claude_code")["models"] == [
        {"id": model_id, "label": label}
        for model_id, label in REGISTRY["claude_code"].available_startup_models
    ]
