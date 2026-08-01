"""Shared snapshot lookups used by candidate providers."""

from __future__ import annotations

import sqlite3

from murder.context_compiler.indexing.queries import (
    get_file_version_by_path,
    list_current_files,
    list_semantic_units_by_path,
    resolve_current_unit_version,
)
from murder.context_compiler.persistence.files import get_file, get_file_version
from murder.context_compiler.persistence.records import (
    SemanticUnitVersionRecord,
)
from murder.context_compiler.persistence.semantic_units import get_semantic_unit


def path_for_file_id(conn: sqlite3.Connection, file_id: int) -> str | None:
    rec = get_file(conn, file_id)
    return rec.path if rec is not None else None


def path_for_file_version_id(conn: sqlite3.Connection, file_version_id: int) -> str | None:
    version = get_file_version(conn, file_version_id)
    if version is None:
        return None
    return path_for_file_id(conn, version.file_id)


def path_for_unit_id(conn: sqlite3.Connection, unit_id: int) -> str | None:
    unit = get_semantic_unit(conn, unit_id)
    if unit is None:
        return None
    return path_for_file_id(conn, unit.file_id)


def unit_and_path_in_snapshot(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    unit_id: int,
) -> tuple[SemanticUnitVersionRecord, str] | None:
    version = resolve_current_unit_version(conn, snapshot_id=snapshot_id, unit_id=unit_id)
    if version is None:
        return None
    path = path_for_unit_id(conn, unit_id)
    if path is None:
        return None
    return version, path


def top_level_units_for_path(
    conn: sqlite3.Connection,
    *,
    snapshot_id: int,
    relative_path: str,
) -> list[SemanticUnitVersionRecord]:
    units = list_semantic_units_by_path(conn, snapshot_id=snapshot_id, relative_path=relative_path)
    top = [u for u in units if u.parent_unit_id is None]
    top.sort(key=lambda u: (u.start_line, u.unit_version_id))
    return top


def snapshot_paths(conn: sqlite3.Connection, snapshot_id: int) -> list[str]:
    return [e.file.path for e in list_current_files(conn, snapshot_id)]


def file_entry_exists(conn: sqlite3.Connection, *, snapshot_id: int, relative_path: str) -> bool:
    return (
        get_file_version_by_path(conn, snapshot_id=snapshot_id, relative_path=relative_path)
        is not None
    )


__all__ = [
    "file_entry_exists",
    "path_for_file_id",
    "path_for_file_version_id",
    "path_for_unit_id",
    "snapshot_paths",
    "top_level_units_for_path",
    "unit_and_path_in_snapshot",
]
