"""Deterministic harness and startup-model routing (no I/O)."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any, cast

from murder.config import HarnessKind, HarnessRoleConfig


def stable_bucket_index(key: str, modulo: int) -> int:
    """Deterministic index for spreading work across a pool."""
    if modulo <= 0:
        raise ValueError("modulo must be positive")
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % modulo


def resolve_default_crow_harness(
    crow_cfg: HarnessRoleConfig, ticket_row: Mapping[str, Any] | None
) -> HarnessKind:
    overt = (ticket_row or {}).get("harness")
    if overt:
        return cast(HarnessKind, overt)
    pool = list(crow_cfg.harnesses) if crow_cfg.harnesses else [crow_cfg.harness]
    tid = str((ticket_row or {}).get("id") or "")
    return pool[stable_bucket_index(tid, len(pool))]


def resolve_default_crow_startup_model(
    crow_cfg: HarnessRoleConfig,
    ticket_row: Mapping[str, Any] | None,
    harness: HarnessKind | None = None,
) -> str | None:
    overt = (ticket_row or {}).get("model")
    if overt:
        return str(overt)
    if harness and crow_cfg.startup_models_by_harness:
        pool = crow_cfg.startup_models_by_harness.get(harness)
        if pool:
            tid = str((ticket_row or {}).get("id") or "")
            if not tid:
                return pool[0]
            return pool[stable_bucket_index(tid, len(pool))]
    if crow_cfg.startup_models:
        pool = crow_cfg.startup_models
        tid = str((ticket_row or {}).get("id") or "")
        if not tid:
            return pool[0]
        return pool[stable_bucket_index(tid, len(pool))]
    return crow_cfg.startup_model


__all__ = [
    "resolve_default_crow_harness",
    "resolve_default_crow_startup_model",
    "stable_bucket_index",
]
