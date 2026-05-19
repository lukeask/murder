"""Persisted user↔agent conversation logs.

An interactive harness (Claude Code, Codex, …) renders its chat in a tmux
pane; ``HarnessAdapter.parse_transcript`` turns a pane capture into an ordered
list of ``(role, text)`` turns. Because ``capture-pane`` only sees a sliding
window of scrollback, each parse is a best-effort snapshot of the *current*
transcript — never a delta. :func:`merge_transcript` reconciles a fresh parse
against what's already persisted so the stored log is the longest, most
complete transcript we've observed.

Known limitation: turns that scroll out of the pane's history before they're
ever captured are not recovered. For the collaborator chat that's fine — we
poll the pane every refresh tick, well within scrollback.

The DB is authoritative; ``db.replace_agent_messages`` / ``db.get_agent_messages``
are the only persistence touchpoints. Roles: ``"user"``, ``"assistant"``,
``"system"``.
"""

from __future__ import annotations

import sqlite3

from murder import db as dbmod

# (role, text) — deliberately a plain tuple for UI transcript rendering.
Turn = tuple[str, str]


def read_conversation(conn: sqlite3.Connection, agent_id: str) -> list[Turn]:
    return [(r["role"], r["body"]) for r in dbmod.get_agent_messages(conn, agent_id)]


def clear(conn: sqlite3.Connection, agent_id: str) -> None:
    """Drop the persisted log for ``agent_id`` — call when a fresh agent
    session starts, so a new run doesn't show the previous run's chat."""
    dbmod.replace_agent_messages(conn, agent_id, [])


def merge_transcript(
    conn: sqlite3.Connection,
    agent_id: str,
    parsed: list[Turn],
    *,
    captured_at: str | None = None,
) -> list[Turn]:
    """Reconcile a fresh full-transcript ``parsed`` against the persisted log.

    Replace the stored log with ``parsed`` when it is at least as long and not
    byte-identical (covers an in-progress reply growing on the last turn). A
    shorter parse is treated as transient pane noise and ignored. Returns the
    effective transcript after merging.
    """
    stored = read_conversation(conn, agent_id)
    if not parsed:
        return stored
    if len(parsed) > len(stored) or (len(parsed) == len(stored) and parsed != stored):
        dbmod.replace_agent_messages(conn, agent_id, parsed, captured_at=captured_at)
        return parsed
    return stored
