"""Independent terminal stream contracts."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from murder.app.protocol.common import ApplicationModel


class TerminalTarget(ApplicationModel):
    """Exact persisted session identity, with an explicit legacy bridge."""

    session_id: UUID | None = None
    legacy_agent_id: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _one_identity(self) -> TerminalTarget:
        if self.session_id is not None and self.legacy_agent_id is not None:
            raise ValueError("terminal target cannot mix session_id and legacy_agent_id")
        return self


class TerminalFrame(ApplicationModel):
    """Authoritative full terminal snapshot.

    The current tmux adapter always emits ``reset=True`` frames.  Replacing the
    rendered pane with ``data`` recovers from any earlier dropped frame.
    """

    type: Literal["terminal.frame"] = "terminal.frame"
    subscription_id: str
    sequence: int = Field(ge=1)
    session_id: UUID | None
    legacy_agent_id: str | None = None
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
    session_id: UUID | None
    legacy_agent_id: str | None = None
    sequence: int = Field(ge=1)
    encoding: Literal["utf-8"] = "utf-8"
    data: str


class TerminalStreamGap(ApplicationModel):
    """Explicit notice that one or more incremental updates were lost."""

    type: Literal["terminal.gap"] = "terminal.gap"
    subscription_id: str
    session_id: UUID | None
    legacy_agent_id: str | None = None
    expected_sequence: int = Field(ge=1)
    next_sequence: int = Field(ge=1)
    snapshot_required: bool = True


__all__ = [
    "TerminalChunk",
    "TerminalFrame",
    "TerminalStreamGap",
    "TerminalTarget",
]
