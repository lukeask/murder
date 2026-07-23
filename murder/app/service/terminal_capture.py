"""Capture terminal viewports by their persisted harness-session identity."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from uuid import UUID

from murder.runtime.terminal import tmux


@dataclass(frozen=True)
class CapturedTerminalFrame:
    data: str
    columns: int
    rows: int


async def capture_persisted_tmux_frame(
    db: sqlite3.Connection,
    session_id: UUID,
) -> CapturedTerminalFrame:
    """Capture a tmux viewport for one persisted harness session.

    Application clients address a terminal by ``harness_sessions.session_id``.
    They never address a transient agent id or a tmux name directly.
    """
    row = db.execute(
        """
        SELECT transport, transport_ref
        FROM harness_sessions
        WHERE session_id = ?
        """,
        (str(session_id),),
    ).fetchone()
    if row is None:
        raise ValueError(f"persisted session {session_id} does not exist")
    if str(row["transport"]) != "tmux":
        raise ValueError(f"session {session_id} does not expose a tmux terminal")
    transport_ref = str(row["transport_ref"])
    data = await tmux.capture_viewport(transport_ref, escapes=True)
    columns, rows = await tmux.pane_dimensions(transport_ref)
    return CapturedTerminalFrame(data=data, columns=columns, rows=rows)


__all__ = ["CapturedTerminalFrame", "capture_persisted_tmux_frame"]
