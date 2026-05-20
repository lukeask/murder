"""Collaborator carving form: YAML sidecar ingest + DB apply compatibility."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import yaml

from murder.persistence import tickets as dbmod
from murder.storage.filesystem import atomic_write_text
from murder.storage.paths import ticket_yaml
from murder.tickets import lifecycle
from murder.tickets.sidecar_sync import reconcile_ticket_yaml
from murder.tickets.status import TicketStatus


class CarveError(ValueError):
    """Invalid carving YAML or ticket state."""


def parse_carve_yaml(text: str) -> dict[str, Any]:
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise CarveError("carve YAML must be a mapping at the top level")
    return raw


def _require_str_list(spec: dict[str, Any], key: str) -> list[str]:
    val = spec.get(key)
    if val is None:
        return []
    if not isinstance(val, list):
        raise CarveError(f"{key} must be a list")
    return [str(x) for x in val]


def _normalize_model(spec: dict[str, Any]) -> str | None:
    m = spec.get("model")
    if m is None:
        return None
    s = str(m).strip()
    return s or None


def _write_yaml_sidecar(repo_root: str, ticket_id: str, spec: dict[str, Any]) -> None:
    path = ticket_yaml(Path(repo_root), ticket_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            existing = dict(raw)
    merged = {**existing, **spec}
    merged["id"] = ticket_id
    atomic_write_text(
        path,
        yaml.safe_dump(merged, sort_keys=False, allow_unicode=False),
    )


def _reconcile_yaml_if_available(
    conn: sqlite3.Connection,
    repo_root: str,
    ticket_id: str,
) -> bool:
    """Compatibility seam for the metadata sync implementation."""
    reconcile_ticket_yaml(conn=conn, repo_root=repo_root, ticket_id=ticket_id)
    return True


def ingest_carve_ready_spec(
    *,
    conn: sqlite3.Connection,
    repo_root: str,
    ticket_id: str,
    spec: dict[str, Any],
) -> TicketStatus:
    """Compatibility ingest path: write sidecar, then reconcile/apply."""
    yaml_id = spec.get("id")
    if yaml_id != ticket_id:
        raise CarveError(f"YAML id {yaml_id!r} does not match target ticket {ticket_id!r}")
    before = dbmod.get_ticket_status(conn, ticket_id)
    sidecar_spec = {**spec, "status": TicketStatus.READY.value}
    _write_yaml_sidecar(repo_root, ticket_id, sidecar_spec)
    if _reconcile_yaml_if_available(conn, repo_root, ticket_id):
        status = dbmod.get_ticket_status(conn, ticket_id)
        if status != TicketStatus.READY.value:
            raise CarveError(
                f"ticket {ticket_id} was reconciled but is not ready (currently {status})"
            )
        return TicketStatus(before) if before is not None else TicketStatus.PLANNED
    return apply_carve_ready_spec(conn, ticket_id, spec)


def apply_carve_ready_spec(
    conn: sqlite3.Connection,
    ticket_id: str,
    spec: dict[str, Any],
) -> TicketStatus:
    """Apply sidecar fields from a parsed carve dict and transition planned → ready.

    Runs in a single transaction. Emits no bus events (callers do that).
    """
    yaml_id = spec.get("id")
    if yaml_id != ticket_id:
        raise CarveError(f"YAML id {yaml_id!r} does not match target ticket {ticket_id!r}")

    row = dbmod.get_ticket(conn, ticket_id)
    if row is None:
        raise CarveError(f"ticket not found: {ticket_id}")
    if row["status"] != TicketStatus.PLANNED.value:
        raise CarveError(f"ticket {ticket_id} must be planned (currently {row['status']})")

    wave_raw = spec.get("wave")
    if wave_raw is None:
        raise CarveError("wave is required in carving YAML")
    try:
        wave = int(wave_raw)
    except (TypeError, ValueError) as e:
        raise CarveError("wave must be an integer") from e
    if wave != int(row["wave"]):
        raise CarveError(f"wave mismatch: YAML has {wave}, DB has {row['wave']}")

    title = spec.get("title")
    if not title or not str(title).strip():
        raise CarveError("title is required in carving YAML")
    title_s = str(title).strip()

    harness_raw = spec.get("harness_override")
    if harness_raw is None:
        harness_raw = spec.get("harness")
    if not harness_raw or not str(harness_raw).strip():
        raise CarveError("harness_override (or harness) is required")
    harness_s = str(harness_raw).strip()

    deps = _require_str_list(spec, "deps")
    skills = _require_str_list(spec, "skills")
    write_set = _require_str_list(spec, "write_set")
    checklist = _require_str_list(spec, "checklist")

    model = _normalize_model(spec)

    conn.execute("BEGIN")
    try:
        dbmod.apply_ticket_carve_payload(
            conn,
            ticket_id,
            title=title_s,
            harness=harness_s,
            model=model,
            deps=deps,
            skills=skills,
            write_set=write_set,
            checklist=checklist,
        )
        prev = lifecycle.transition(conn, ticket_id, TicketStatus.READY)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return prev
