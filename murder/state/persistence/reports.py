"""Persistence for the reports and report_revisions tables.

Thin binding over ``_doc_dao`` at full parity with notes.
"""

from __future__ import annotations

from typing import Any

from murder.state.persistence._doc_dao import (
    get_doc,
    insert_revision,
    latest_doc_name,
    list_docs,
    list_revisions,
    mark_doc_retired,
    rename_doc,
    upsert_doc,
)
from murder.state.persistence.connection import RepoDb

# Trusted constants — never wire input.
_TABLE = "reports"
_REVISIONS_TABLE = "report_revisions"
_FK_COL = "report_name"


def get_report(db: RepoDb, name: str) -> dict[str, Any] | None:
    return get_doc(db, _TABLE, name)


def list_reports(db: RepoDb) -> list[dict[str, Any]]:
    return list_docs(db, _TABLE)


def latest_report_name(db: RepoDb) -> str | None:
    return latest_doc_name(db, _TABLE)


def upsert_report(db: RepoDb, name: str, *, body: str, materialized_path: str) -> None:
    upsert_doc(db, _TABLE, name, body=body, materialized_path=materialized_path)


def rename_report(db: RepoDb, old_name: str, new_name: str, *, materialized_path: str) -> None:
    rename_doc(
        db,
        _TABLE,
        _REVISIONS_TABLE,
        _FK_COL,
        old_name,
        new_name,
        materialized_path=materialized_path,
    )


def mark_report_retired(db: RepoDb, name: str, *, materialized_path: str) -> None:
    mark_doc_retired(db, _TABLE, name, materialized_path=materialized_path)


def insert_report_revision(
    db: RepoDb,
    name: str,
    *,
    source: str,
    body: str,
    content_hash: str,
) -> int:
    return insert_revision(
        db, _REVISIONS_TABLE, _FK_COL, name, source=source, body=body, content_hash=content_hash
    )


def list_report_revisions(db: RepoDb, name: str) -> list[dict[str, Any]]:
    return list_revisions(db, _REVISIONS_TABLE, _FK_COL, name)
