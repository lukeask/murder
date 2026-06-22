"""Harness-models snapshot builder."""

from __future__ import annotations

from contextlib import closing

from ._common import LOGGER, ReadModelBase


class HarnessReadModel(ReadModelBase):
    """Build the harness-models RPC payload."""

    def get_harness_models_snapshot(self) -> dict[str, object]:
        """Return the locked RPC payload for ``state.harness_models_snapshot``.

        Shape (wrapped by ``_value(...)`` in the host)::

            {
              "models": {
                "<harness_kind>": [{"id": "...", "label": "..."}, ...],
                ...
              },
              "as_of": "<ISO8601 UTC string>" | null
            }

        *as_of* is the most-recent ``fetched_at`` across all rows (null when
        the table is empty or does not yet exist). Only harnesses that have
        been persisted appear as keys; a missing key is valid — the frontend
        falls back to the classvar default.
        """
        import json as _json

        with closing(self._connect()) as conn:
            # Guard: table may not exist on an old DB (idempotent CREATE TABLE
            # runs at init_db, but get_db does not call init_db).
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='harness_models'"
            ).fetchone()
            if not table_exists:
                return {"models": {}, "as_of": None}
            rows = conn.execute(
                "SELECT harness, fetched_at, models_json FROM harness_models"
            ).fetchall()

        if not rows:
            return {"models": {}, "as_of": None}

        models_map: dict[str, list[dict[str, str]]] = {}
        fetched_timestamps: list[str] = []

        for row in rows:
            harness = str(row["harness"])
            fetched_timestamps.append(str(row["fetched_at"]))
            try:
                models = _json.loads(str(row["models_json"] or "[]"))
            except (ValueError, TypeError):
                LOGGER.debug("harness_models row %r has unparseable models_json", harness)
                models = []
            models_map[harness] = models

        as_of = max(fetched_timestamps) if fetched_timestamps else None
        return {"models": models_map, "as_of": as_of}
