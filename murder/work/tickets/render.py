"""Render unified ticket markdown fragments."""

from __future__ import annotations

from typing import Any

import yaml

from murder.work.tickets.parser import ParsedTicket

_FRONTMATTER_DELIM = "---"
_FRONTMATTER_KEYS = ("title", "deps", "harness", "model", "worktree", "parent")


def render_ticket_frontmatter(ticket: ParsedTicket | dict[str, Any]) -> str:
    """Render canonical ticket frontmatter with exactly the agent-authored fields."""
    payload = _payload(ticket)
    yaml_text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=False).strip()
    return f"{_FRONTMATTER_DELIM}\n{yaml_text}\n{_FRONTMATTER_DELIM}\n"


def _payload(ticket: ParsedTicket | dict[str, Any]) -> dict[str, Any]:
    if isinstance(ticket, ParsedTicket):
        values = {
            "title": ticket.title,
            "deps": list(ticket.deps),
            "harness": ticket.harness,
            "model": ticket.model,
            "worktree": ticket.worktree,
            "parent": ticket.parent,
        }
    else:
        values = {
            "title": ticket.get("title"),
            "deps": ticket.get("deps"),
            "harness": ticket.get("harness"),
            "model": ticket.get("model"),
            "worktree": ticket.get("worktree"),
            "parent": ticket.get("parent"),
        }

    return {key: _render_value(key, values.get(key)) for key in _FRONTMATTER_KEYS}


def _render_value(key: str, value: object) -> object:
    if key == "deps":
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
        if value is None:
            return []
        return [str(value)]
    if value is None:
        return None
    return str(value)
