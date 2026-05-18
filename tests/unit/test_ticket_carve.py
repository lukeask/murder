"""Carving YAML: sidecar replace + planned → ready."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from murder import db as dbmod
from murder.bus import TicketStatus
from murder.storage.paths import ticket_yaml
from murder.tickets.carve import (
    CarveError,
    apply_carve_ready_spec,
    ingest_carve_ready_spec,
    parse_carve_yaml,
)
from murder.tui.dispatch.roster import parse_carve_paste


def test_parse_carve_paste_json() -> None:
    d = parse_carve_paste('  {\n  "id": "t1", "title": "Hi"\n}  ')
    assert d["id"] == "t1"


def test_parse_carve_paste_yaml() -> None:
    d = parse_carve_paste("id: t2\ntitle: Yo\n")
    assert d["title"] == "Yo"


def _insert_ticket(
    conn: sqlite3.Connection,
    ticket_id: str,
    *,
    wave: int = 1,
    status: str = "planned",
) -> None:
    conn.execute(
        """
        INSERT INTO tickets(
            id, title, wave, status, harness, model, attempts, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, NULL, NULL, 0, '2026-01-01T00:00:00', '2026-01-01T00:00:00')
        """,
        (ticket_id, f"title-{ticket_id}", wave, status),
    )


def test_parse_carve_yaml_rejects_non_mapping(memdb: sqlite3.Connection) -> None:
    del memdb
    with pytest.raises(CarveError, match="mapping"):
        parse_carve_yaml("- a list")


def test_apply_carve_ready_updates_sidecar_and_status(memdb: sqlite3.Connection) -> None:
    _insert_ticket(memdb, "t001", wave=1, status="done")
    _insert_ticket(memdb, "t002", wave=2, status="planned")
    memdb.execute("INSERT INTO ticket_deps(ticket_id, depends_on_id) VALUES ('t002', 't001')")
    yaml = """
id: t002
title: "Ship feature"
wave: 2
harness_override: cursor
model: "Composer 2"
deps: [t001]
skills: []
write_set:
  - murder/db.py
checklist:
  - Do the thing
  - Test the thing
"""
    spec = parse_carve_yaml(yaml)
    prev = apply_carve_ready_spec(memdb, "t002", spec)
    assert prev == TicketStatus.PLANNED

    row = memdb.execute(
        "SELECT status, title, harness, model FROM tickets WHERE id='t002'"
    ).fetchone()
    assert row["status"] == "ready"
    assert row["title"] == "Ship feature"
    assert row["harness"] == "cursor"
    assert row["model"] == "Composer 2"

    deps = [
        r["depends_on_id"]
        for r in memdb.execute(
            "SELECT depends_on_id FROM ticket_deps WHERE ticket_id='t002'"
        ).fetchall()
    ]
    assert deps == ["t001"]

    paths = [
        r["path"]
        for r in memdb.execute(
            "SELECT path FROM ticket_write_set WHERE ticket_id='t002'"
        ).fetchall()
    ]
    assert paths == ["murder/db.py"]

    texts = [
        r["text"]
        for r in memdb.execute(
            "SELECT text FROM checklist WHERE ticket_id='t002' ORDER BY ord"
        ).fetchall()
    ]
    assert texts == ["Do the thing", "Test the thing"]


def test_apply_carve_rejects_wave_mismatch(memdb: sqlite3.Connection) -> None:
    _insert_ticket(memdb, "t009", wave=3, status="planned")
    spec = parse_carve_yaml(
        "id: t009\ntitle: X\nwave: 99\nharness_override: cursor\nchecklist: []\ndeps: []\n"
        "write_set: []\nskills: []\n"
    )
    with pytest.raises(CarveError, match="wave mismatch"):
        apply_carve_ready_spec(memdb, "t009", spec)


def test_ingest_carve_writes_yaml_sidecar_and_merges(
    memdb: sqlite3.Connection, tmp_path: Path
) -> None:
    _insert_ticket(memdb, "t010", wave=1, status="planned")
    ypath = ticket_yaml(tmp_path, "t010")
    ypath.parent.mkdir(parents=True, exist_ok=True)
    ypath.write_text("id: t010\nstatus: planned\nnote: keep-me\n", encoding="utf-8")

    spec = parse_carve_yaml(
        "id: t010\ntitle: New\nwave: 1\nharness: codex\ndeps: []\nskills: []\n"
        "write_set: [murder/cli.py]\nchecklist: [done]\n"
    )
    prev = ingest_carve_ready_spec(
        conn=memdb,
        repo_root=str(tmp_path),
        ticket_id="t010",
        spec=spec,
    )

    assert prev == TicketStatus.PLANNED
    row = memdb.execute("SELECT status FROM tickets WHERE id='t010'").fetchone()
    assert row["status"] == "ready"
    ytxt = ypath.read_text(encoding="utf-8")
    assert "note: keep-me" in ytxt
    assert "title: New" in ytxt


def test_compute_ready_excludes_planned(memdb: sqlite3.Connection) -> None:
    _insert_ticket(memdb, "t100", status="planned")
    _insert_ticket(memdb, "t101", status="ready")
    assert dbmod.compute_ready(memdb) == ["t101"]
