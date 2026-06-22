"""Shared helpers, constants, and base classes for read_models builders."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from murder.app.service.client_api import CrowSessionSummary
from murder.state.persistence.schema import get_db

LOGGER = logging.getLogger(__name__)

# Ticket states that indicate the work item is closed; a failed agent on such a
# ticket is droppable once its heartbeat goes stale.
TERMINAL_TICKET_STATUSES = frozenset({"done", "failed"})

# Hide failed agents after this long without a recent heartbeat.
FAILED_STALE_AFTER = timedelta(hours=2)

# A still-OPEN user intention older than this (and not explicitly dismissed) is
# surfaced as STALE — the zero-LLM "fell through the cracks" radar. v0 taxonomy.
STALE_AFTER_HOURS = 48

# The harness kind whose graceful-exit sessions can be resumed (/resume keybind,
# built on this DTO's resumability triple). Mirrors ClaudeCodeAdapter.kind.
RESUMABLE_HARNESS = "claude_code"

_FRONTMATTER_DELIM = "---"


class GenerationKeys:
    """Shared invalidation-generation state for all read-model builders."""

    def __init__(self) -> None:
        self._generations: dict[str, int] = defaultdict(int)

    def invalidate(self, key: str) -> None:
        self._generations[key] += 1

    def current_key(self, scope: str) -> str:
        return f"{scope}-{self._generations[scope]}"


class ReadModelBase:
    """Base class for per-domain snapshot builders."""

    def __init__(self, db_path: Path, keys: GenerationKeys) -> None:
        self.db_path = Path(db_path)
        self.keys = keys

    def _connect(self) -> sqlite3.Connection:
        return get_db(self.db_path)


def _keep_failed_session(session: CrowSessionSummary, *, now: datetime) -> bool:
    """Whether a failed agent should remain on the wire roster.

    Roster predicate: keep failed agents whose ticket is still active, or
    whose heartbeat is recent; drop the rest. ``now`` and the
    session timestamps are all naive UTC (see ``datetime.utcnow``), so they are
    compared directly without tz normalisation.
    """
    if session.status != "failed":
        return True
    ticket_status = session.ticket_status or ""
    if ticket_status and ticket_status not in TERMINAL_TICKET_STATUSES:
        return True
    last_seen = session.last_seen or session.started_at
    if last_seen is None:
        return True
    return now - last_seen <= FAILED_STALE_AFTER


def _plan_parent_from_frontmatter(frontmatter_json: object) -> str | None:
    """Extract a plan's parent-plan name from its persisted frontmatter.

    The plans table holds no dedicated parent column; the only non-derived parent
    metadata is a `parent` key in the plan's frontmatter (C11 expects the parent
    plan's NAME or null). Returns None when absent, blank, or non-string.
    """
    if not isinstance(frontmatter_json, str) or not frontmatter_json:
        return None
    try:
        data = json.loads(frontmatter_json)
    except (ValueError, TypeError):
        LOGGER.debug("plan frontmatter_json failed to parse; treating parent as None")
        return None
    if not isinstance(data, dict):
        return None
    parent = data.get("parent")
    if isinstance(parent, str) and parent.strip():
        return parent.strip()
    return None


def _strip_frontmatter(md_text: str) -> str:
    """Return the ticket body with leading YAML frontmatter removed.

    Mirrors ``murder.work.tickets.parser._split_frontmatter`` so the C8 editor
    receives exactly the frontmatter-stripped body (preserving the ``# Checklist``
    section). Falls back to the whole text when there is no valid frontmatter block.
    """
    if not md_text.startswith(f"{_FRONTMATTER_DELIM}\n"):
        return md_text
    try:
        _front, body = md_text[4:].split(f"\n{_FRONTMATTER_DELIM}", 1)
    except ValueError:
        return md_text
    if body.startswith("\n"):
        body = body[1:]
    return body


def _extract_user_text(payload_json: object) -> str:
    """Extract the user turn's text from a stored block payload.

    User blocks are stored as ``{"type": "user", "text": ...}`` (see
    ``conversation.append_user_message``). Returns the stripped text, or the
    empty string if the payload is malformed or has no text.
    """
    if not isinstance(payload_json, str) or not payload_json:
        return ""
    try:
        data = json.loads(payload_json)
    except (ValueError, TypeError):
        LOGGER.debug("user-block payload_json failed to parse; returning empty text")
        return ""
    if not isinstance(data, dict):
        return ""
    text = data.get("text")
    return text.strip() if isinstance(text, str) else ""


def _is_noise(text: str) -> bool:
    """Whether a user line is command-ish noise the feed should drop.

    Skips empty/whitespace lines and command-ish lines (leading ``!`` or ``:``).
    Keeps ``@…`` lines — those are intentions aimed at a target, the feed's whole
    point. Mirrors the plan's server-side noise filter.
    """
    if not text:
        return True
    return text[0] in ("!", ":")


def _is_stale(ts: str, stale_before: datetime) -> bool:
    """Whether a user block's timestamp is older than the stale cutoff.

    A malformed/missing timestamp is treated as NOT stale (better to surface an
    OPEN item than to hide it as stale on a parse failure).
    """
    parsed = _parse_datetime(ts)
    if parsed is None:
        return False
    return parsed < stale_before


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
