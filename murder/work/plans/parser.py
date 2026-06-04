"""Plan markdown parser/writer."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

from murder.work.plans.schema import Plan, PlanStatus

_FRONTMATTER_DELIM = "---"


def parse(md_text: str, *, default_name: str | None = None) -> Plan:
    """Parse a YAML-frontmatter + body markdown file into a Plan."""
    if not md_text.startswith(f"{_FRONTMATTER_DELIM}\n"):
        raise ValueError("plan markdown must start with YAML frontmatter")
    try:
        front_text, body = md_text[4:].split(f"\n{_FRONTMATTER_DELIM}", 1)
    except ValueError as exc:
        raise ValueError("plan markdown is missing closing frontmatter delimiter") from exc
    if body.startswith("\n"):
        body = body[1:]
    raw = yaml.safe_load(front_text) or {}
    if not isinstance(raw, dict):
        raise ValueError("plan frontmatter must be a mapping")
    name = raw.get("name") or default_name
    if not isinstance(name, str) or not name.strip():
        raise ValueError("plan frontmatter requires a non-empty name")
    status_raw = raw.get("status", PlanStatus.DRAFT.value)
    try:
        status = PlanStatus(str(status_raw))
    except ValueError as exc:
        raise ValueError(f"invalid plan status: {status_raw}") from exc
    created_at = _parse_dt(raw.get("created_at")) or datetime.utcnow()
    updated_at = _parse_dt(raw.get("updated_at"))
    related = raw.get("related_tickets", [])
    if related is None:
        related = []
    if not isinstance(related, list) or not all(isinstance(x, str) for x in related):
        raise ValueError("related_tickets must be a list of strings")
    revisions = raw.get("revisions", 0)
    if not isinstance(revisions, int):
        raise ValueError("revisions must be an integer")
    known = {"name", "status", "created_at", "updated_at", "revisions", "related_tickets"}
    extras = {str(k): v for k, v in raw.items() if k not in known}
    return Plan(
        name=name.strip(),
        status=status,
        created_at=created_at,
        updated_at=updated_at,
        revisions=revisions,
        related_tickets=related,
        frontmatter=extras,
        body=body,
    )


def render(plan: Plan) -> str:
    """Emit a plan back to canonical YAML-frontmatter markdown."""
    front: dict[str, object] = dict(plan.frontmatter)
    front.update(
        {
            "name": plan.name,
            "status": plan.status.value,
            "created_at": plan.created_at.isoformat(timespec="seconds"),
            "revisions": plan.revisions,
            "related_tickets": list(plan.related_tickets),
        }
    )
    if plan.updated_at is not None:
        front["updated_at"] = plan.updated_at.isoformat(timespec="seconds")
    ordered = {k: front[k] for k in sorted(front)}
    yaml_text = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=False).strip()
    body = plan.body
    if body and not body.endswith("\n"):
        body += "\n"
    return f"{_FRONTMATTER_DELIM}\n{yaml_text}\n{_FRONTMATTER_DELIM}\n{body}"


def read(path: Path) -> Plan:
    return parse(path.read_text(encoding="utf-8"), default_name=path.stem)


def write(path: Path, plan: Plan) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(render(plan))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def new_plan(name: str) -> Plan:
    return Plan(name=name, created_at=datetime.utcnow(), status=PlanStatus.DRAFT)


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("datetime values must be ISO strings")
