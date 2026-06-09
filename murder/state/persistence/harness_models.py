"""Persistence for the harness_models table.

Stores per-harness model discovery results so the Ink frontend can pull
last-good values instantly without waiting for a live probe.

Each row represents the most-recent discovery attempt for one harness kind.
``models_json`` is a JSON array of ``{"id": ..., "label": ...}`` objects.
``discovery_error`` is non-null when the last probe failed.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def upsert_harness_models(
    conn: sqlite3.Connection,
    *,
    harness: str,
    models: list[dict[str, str]],
    fetched_at: str | None = None,
    discovery_error: str | None = None,
) -> None:
    """Insert or replace the discovery row for *harness*.

    *models* must be a list of ``{"id": ..., "label": ...}`` dicts.
    Pass ``discovery_error`` when the probe failed; it is stored in the row
    alongside whatever models were available (classvar fallback or empty).
    """
    ts = fetched_at or _now()
    conn.execute(
        """
        INSERT INTO harness_models (harness, fetched_at, models_json, discovery_error)
             VALUES (?, ?, ?, ?)
             ON CONFLICT(harness) DO UPDATE SET
                 fetched_at      = excluded.fetched_at,
                 models_json     = excluded.models_json,
                 discovery_error = excluded.discovery_error
        """,
        (harness, ts, json.dumps(models), discovery_error),
    )


def get_all_harness_models(
    conn: sqlite3.Connection,
) -> list[dict[str, object]]:
    """Return all rows from ``harness_models`` as plain dicts.

    Each dict has keys: ``harness``, ``fetched_at``, ``models`` (list of
    ``{"id", "label"}``), ``discovery_error``.
    """
    rows = conn.execute(
        "SELECT harness, fetched_at, models_json, discovery_error FROM harness_models"
    ).fetchall()
    result = []
    for row in rows:
        try:
            models = json.loads(str(row["models_json"] or "[]"))
        except (ValueError, TypeError):
            models = []
        result.append(
            {
                "harness": str(row["harness"]),
                "fetched_at": str(row["fetched_at"]),
                "models": models,
                "discovery_error": row["discovery_error"],
            }
        )
    return result


__all__ = ["upsert_harness_models", "get_all_harness_models"]
