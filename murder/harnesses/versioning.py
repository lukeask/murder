"""Harness version registry and adapter resolution.

Consumers call ``resolve_adapter_id(kind, version)`` to get the right adapter
identifier given an installed CLI version.  In Phase 1 all adapter IDs resolve
to a single class per kind; the infrastructure is in place so Phase 2 can add
per-version adapter classes without touching call sites.

See verified_versions.yaml for the committed version manifest and
.murder/notes/plan-harness-versioning-phase2.md for Phase 2 plans.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

_log = logging.getLogger(__name__)

_MANIFEST_PATH = Path(__file__).parent / "verified_versions.yaml"

_SEMVER_RE = re.compile(r"(\d+\.\d+(?:\.\d+)*)")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HarnessVersionRecord:
    """One probed harness version, resolved to an adapter identifier."""

    kind: str
    raw: str
    normalized: str
    verified: bool
    adapter_id: str
    probed_at: datetime


class HarnessVersionRegistry:
    """In-memory store replaced atomically after each probe cycle.

    Callers get a snapshot view; no locking needed in a single asyncio loop.
    """

    def __init__(self) -> None:
        self._records: dict[str, HarnessVersionRecord] = {}

    def replace(self, records: list[HarnessVersionRecord]) -> None:
        self._records = {r.kind: r for r in records}

    def get(self, kind: str) -> HarnessVersionRecord | None:
        return self._records.get(kind)

    def all(self) -> list[HarnessVersionRecord]:
        return list(self._records.values())

    def is_empty(self) -> bool:
        return not self._records


# ---------------------------------------------------------------------------
# Manifest loading
# ---------------------------------------------------------------------------


def load_manifest(path: Path = _MANIFEST_PATH) -> dict[str, Any]:
    """Load verified_versions.yaml; return empty dict if missing."""
    try:
        with path.open() as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        _log.warning("verified_versions.yaml not found at %s", path)
        return {}


# ---------------------------------------------------------------------------
# Version normalisation
# ---------------------------------------------------------------------------


def normalize_version(raw: str) -> str:
    """Extract the first X.Y.Z semver pattern from raw --version output."""
    m = _SEMVER_RE.search(raw)
    return m.group(1) if m else raw.strip()


# ---------------------------------------------------------------------------
# Adapter resolution
# ---------------------------------------------------------------------------


def _version_tuple(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def resolve_adapter_id(
    kind: str,
    version: str | None,
    manifest: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    """Return ``(adapter_id, verified)`` for *kind* at *version*.

    ``verified`` is True only on an exact manifest match.

    Forward-compat rule: if installed version > highest manifest key, use the
    latest adapter and log ``version_newer_than_supported``.
    """
    if manifest is None:
        manifest = load_manifest()

    kind_entries: dict[str, Any] = manifest.get(kind) or {}
    if not kind_entries:
        return f"{kind}_default", False

    sorted_keys = sorted(kind_entries, key=_version_tuple)
    latest_key = sorted_keys[-1]
    latest_adapter: str = kind_entries[latest_key]["adapter"]

    if version is None:
        return latest_adapter, False

    if version in kind_entries:
        return kind_entries[version]["adapter"], True

    installed = _version_tuple(version)
    max_verified = _version_tuple(latest_key)

    if installed > max_verified:
        _log.warning(
            "harness %s: installed version %s is newer than highest verified %s; "
            "using adapter %s (forward-compat — run check_harness_versions.py)",
            kind,
            version,
            latest_key,
            latest_adapter,
        )
        return latest_adapter, False

    # Find the latest manifest entry at or below the installed version
    at_or_below = [k for k in sorted_keys if _version_tuple(k) <= installed]
    if at_or_below:
        return kind_entries[at_or_below[-1]]["adapter"], False

    # Installed is older than every manifest entry — use earliest
    return kind_entries[sorted_keys[0]]["adapter"], False


__all__ = [
    "HarnessVersionRecord",
    "HarnessVersionRegistry",
    "load_manifest",
    "normalize_version",
    "resolve_adapter_id",
]
