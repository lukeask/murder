"""Collaborator carving form: legacy YAML ingest + DB apply compatibility."""

from __future__ import annotations

import sqlite3
from typing import Any

import yaml

from murder.state.persistence import tickets as dbmod
from murder.work.tickets import lifecycle
from murder.work.tickets.status import TicketStatus


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


def ingest_carve_ready_spec(
    *,
    conn: sqlite3.Connection,
    repo_root: str,
    ticket_id: str,
    spec: dict[str, Any],
) -> TicketStatus:
    """Compatibility ingest path: apply the parsed legacy YAML directly."""
    yaml_id = spec.get("id")
    if yaml_id != ticket_id:
        raise CarveError(f"YAML id {yaml_id!r} does not match target ticket {ticket_id!r}")
    del repo_root
    return apply_carve_ready_spec(conn, ticket_id, spec)


def apply_carve_ready_spec(
    conn: sqlite3.Connection,
    ticket_id: str,
    spec: dict[str, Any],
) -> TicketStatus:
    """Apply fields from a parsed carve dict and transition planned → ready.

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
            checklist=checklist,
        )
        prev = lifecycle.transition(conn, ticket_id, TicketStatus.READY)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return prev
