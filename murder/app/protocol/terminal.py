"""Independent terminal stream contracts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from murder.app.protocol.common import ApplicationModel


class TerminalTarget(ApplicationModel):
    """Exact persisted session identity for a terminal stream."""

    session_id: UUID


class TerminalFrame(ApplicationModel):
    """Authoritative full terminal snapshot.

    The current tmux adapter always emits ``reset=True`` frames.  Replacing the
    rendered pane with ``data`` recovers from any earlier dropped frame.
    """

    type: Literal["terminal.frame"] = "terminal.frame"
    subscription_id: str
    sequence: int = Field(ge=1)
    session_id: UUID
    captured_at: AwareDatetime
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)
    # The current service emits decoded capture-pane text.  Do not advertise
    # base64 until both renderers implement byte decoding.
    encoding: Literal["utf-8"] = "utf-8"
    data: str
    reset: bool = True


class TerminalChunk(ApplicationModel):
    """A future incremental terminal update.

    Chunks are usable only when ``sequence`` immediately follows the last
    accepted update.  A discontinuity requires a full snapshot.
    """

    type: Literal["terminal.chunk"] = "terminal.chunk"
    subscription_id: str
    session_id: UUID
    sequence: int = Field(ge=1)
    encoding: Literal["utf-8"] = "utf-8"
    data: str


class TerminalStreamGap(ApplicationModel):
    """Explicit notice that one or more incremental updates were lost."""

    type: Literal["terminal.gap"] = "terminal.gap"
    subscription_id: str
    session_id: UUID
    expected_sequence: int = Field(ge=1)
    next_sequence: int = Field(ge=1)
    snapshot_required: bool = True


__all__ = [
    "TerminalChunk",
    "TerminalFrame",
    "TerminalStreamGap",
    "TerminalTarget",
]
