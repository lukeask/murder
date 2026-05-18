"""Escalation queue helpers.

Escalations live in two places:
- DB row in `escalations` (metadata: severity, recipient, source_event_id, resolved).
- Markdown body at `.murder/escalations/<id>.md` for `to_recipient='collaborator'`
  (Collaborator reads markdown; user reads the TUI strip).

Sentinel emits via `Bus.publish(EscalationEvent)` and a runtime hook
inserts the DB row + (if collaborator) writes the .md body.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from murder.bus import EscalationEvent


def queue_for_user(conn: sqlite3.Connection, event: EscalationEvent) -> int:
    """Insert escalation row; return id. TUI strip will pick it up via bus subscription."""
    # TODO(M3): db.insert_escalation with to_recipient='user', source_event_id from event.
    raise NotImplementedError("M3: escalations.queue_for_user")


def queue_for_collaborator(
    conn: sqlite3.Connection,
    event: EscalationEvent,
    body: str,
    repo_root: Path,
) -> tuple[int, Path]:
    """Insert escalation row + write `.murder/escalations/<id>.md` with body."""
    # TODO(M3): write body atomically (tempfile + os.replace); return (id, path).
    # Body content convention: top-level title = reason; section per supporting fact.
    raise NotImplementedError("M3: escalations.queue_for_collaborator")


def list_pending(conn: sqlite3.Connection, recipient: str | None = None) -> list[dict]:
    # TODO(M3): SELECT * FROM escalations WHERE resolved = 0 [AND to_recipient = ?].
    raise NotImplementedError("M3: escalations.list_pending")


def resolve(conn: sqlite3.Connection, escalation_id: int) -> None:
    """Mark resolved; sets resolved_at to now."""
    # TODO(M3)
    raise NotImplementedError("M3: escalations.resolve")
