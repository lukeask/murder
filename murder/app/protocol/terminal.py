"""Independent terminal stream contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from murder.app.protocol.common import ApplicationModel


class TerminalTarget(ApplicationModel):
    session_id: str | None = Field(default=None, max_length=200)


class TerminalFrame(ApplicationModel):
    mode: Literal["replace"] = "replace"
    sequence: int = Field(ge=1)
    session_id: str
    frame: str
