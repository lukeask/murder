"""SCHEMA_SQL applies cleanly; FKs and CHECKs do their job."""

from __future__ import annotations

import sqlite3

import pytest


def test_schema_idempotent() -> None:
    # TODO(M0): apply SCHEMA_SQL twice; assert no error (CREATE IF NOT EXISTS).
    pytest.skip("M0 stub")


def test_status_check_constraint_rejects_garbage(memdb: sqlite3.Connection) -> None:
    # TODO(M0): INSERT into tickets with status='nonsense' → IntegrityError.
    pytest.skip("M0 stub")


def test_ticket_dep_self_loop_rejected(memdb: sqlite3.Connection) -> None:
    # TODO(M0): INSERT INTO ticket_deps (id, id) → CHECK violation.
    pytest.skip("M0 stub")


def test_cascade_deletes(memdb: sqlite3.Connection) -> None:
    # TODO(M0): delete a ticket; verify ticket_deps + write_set + skills + checklist clean.
    pytest.skip("M0 stub")
