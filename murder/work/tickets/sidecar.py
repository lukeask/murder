"""Ticket metadata YAML parser/renderer for `.murder/tickets/<id>.yaml`."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import yaml

from murder.work.tickets.status import TicketStatus

_FILE_AUTHORED_STATUSES = {TicketStatus.PLANNED, TicketStatus.READY}


class TicketMetadataError(ValueError):
    """Invalid ticket metadata payload."""


@dataclass(frozen=True, slots=True)
class TicketMetadata:
    id: str
    title: str
    wave: int
    status: TicketStatus
    harness: str | None = None
    model: str | None = None
    deps: list[str] | None = None
    skills: list[str] | None = None
    checklist: list[str] | None = None
    schedule_at: str | None = None

    def __post_init__(self) -> None:
        # Normalize optional list fields to empty lists for stable rendering.
        object.__setattr__(self, "deps", list(self.deps or []))
        object.__setattr__(self, "skills", list(self.skills or []))
        object.__setattr__(self, "checklist", list(self.checklist or []))


def parse_ticket_metadata(text: str, *, expected_id: str | None = None) -> TicketMetadata:
    raw = yaml.safe_load(text)
    if not isinstance(raw, dict):
        raise TicketMetadataError("ticket metadata YAML must be a mapping")

    ticket_id = _require_non_empty_str(raw.get("id"), "id")
    if expected_id is not None and ticket_id != expected_id:
        raise TicketMetadataError(
            f"ticket metadata id {ticket_id!r} does not match expected id {expected_id!r}"
        )

    title = _require_non_empty_str(raw.get("title"), "title")
    wave = _require_int(raw.get("wave"), "wave")
    status = _require_status(raw.get("status"))
    harness = _optional_str(raw.get("harness"))
    if harness is None:
        harness = _optional_str(raw.get("harness_override"))
    model = _optional_str(raw.get("model"))
    deps = _require_str_list(raw.get("deps"), "deps")
    skills = _require_str_list(raw.get("skills"), "skills")
    checklist = _require_str_list(raw.get("checklist"), "checklist")
    schedule_at = _require_schedule_at(raw.get("schedule_at"))

    return TicketMetadata(
        id=ticket_id,
        title=title,
        wave=wave,
        status=status,
        harness=harness,
        model=model,
        deps=deps,
        skills=skills,
        checklist=checklist,
        schedule_at=schedule_at,
    )


def ensure_file_authored_status(status: TicketStatus) -> None:
    if status not in _FILE_AUTHORED_STATUSES:
        raise TicketMetadataError(
            f"file-authored status must be one of: planned, ready (got {status.value!r})"
        )


def render_ticket_metadata(meta: TicketMetadata) -> str:
    payload: dict[str, Any] = {
        "id": meta.id,
        "title": meta.title,
        "wave": meta.wave,
        "status": meta.status.value,
        "harness": meta.harness,
        "model": meta.model,
        "deps": list(meta.deps or []),
        "skills": list(meta.skills or []),
        "checklist": list(meta.checklist or []),
        "schedule_at": meta.schedule_at,
    }
    return yaml.safe_dump(payload, sort_keys=False, allow_unicode=False)


def ticket_metadata_hash(meta: TicketMetadata) -> str:
    return hashlib.sha256(render_ticket_metadata(meta).encode("utf-8")).hexdigest()


def _require_non_empty_str(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TicketMetadataError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TicketMetadataError("optional string fields must be strings when present")
    s = value.strip()
    return s or None


def _require_int(value: object, field: str) -> int:
    if not isinstance(value, int):
        raise TicketMetadataError(f"{field} must be an integer")
    return value


def _require_status(value: object) -> TicketStatus:
    if not isinstance(value, str):
        raise TicketMetadataError("status must be a string")
    try:
        return TicketStatus(value)
    except ValueError as exc:
        raise TicketMetadataError(f"invalid ticket status: {value!r}") from exc


def _require_str_list(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise TicketMetadataError(f"{field} must be a list of strings")
    return list(value)


def _require_schedule_at(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TicketMetadataError("schedule_at must be null or an ISO timestamp string")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise TicketMetadataError("schedule_at must be a valid ISO timestamp") from exc
    return value
