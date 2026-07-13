"""Configured harness model catalog.

This module intentionally does not start harnesses, capture panes, or emit
terminal input.  A harness's model list is configuration supplied by its
adapter class and is persisted so settings and the frontend share one durable
catalog.  Runtime verification of a requested model belongs exclusively to
the verified model-selection operation.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from murder.llm.harnesses import REGISTRY
from murder.state.persistence.harness_models import upsert_harness_models

LOGGER = logging.getLogger(__name__)

CATALOG_PROVENANCE = "configured_catalog"
CATALOG_ADVISORY = "configured_catalog: static adapter catalog; not verified from a live harness"


def configured_harnesses() -> list[str]:
    """Return every registered harness represented by the configured catalog."""

    return sorted(REGISTRY)


def get_available_models(harness: str) -> list[tuple[str, str]]:
    """Return a fresh copy of the harness's configured static model catalog."""

    adapter_cls = REGISTRY.get(harness)
    return list(adapter_cls.available_startup_models) if adapter_cls is not None else []


async def refresh_and_persist_harness_models(
    repo_root: Path,
    db: sqlite3.Connection | None = None,
) -> None:
    """Persist configured catalog rows with explicit non-live provenance.

    ``repo_root`` remains part of the service-facing interface because this
    routine is scheduled by project lifecycle code; catalog construction itself
    deliberately performs no repository or terminal I/O.
    """

    del repo_root
    if db is None:
        return
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for harness in configured_harnesses():
        models = [
            {"id": model_id, "label": label} for model_id, label in get_available_models(harness)
        ]
        upsert_harness_models(
            db,
            harness=harness,
            models=models,
            fetched_at=fetched_at,
            discovery_error=CATALOG_ADVISORY,
        )
    db.commit()


__all__ = [
    "CATALOG_ADVISORY",
    "CATALOG_PROVENANCE",
    "configured_harnesses",
    "get_available_models",
    "refresh_and_persist_harness_models",
]
