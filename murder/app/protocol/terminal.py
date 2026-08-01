"""Independent, resumable terminal-output stream contracts.

The stream has one stable ``stream_id`` (on the enclosing wire message) and a
strictly increasing sequence space.  A keyframe is a complete renderer state;
chunks are opaque terminal bytes and must not be interpreted by the protocol.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from murder.app.protocol.common import ApplicationModel


class TerminalTarget(ApplicationModel):
    """Exact persisted session identity for a terminal stream."""

    session_id: UUID


class TerminalColor(ApplicationModel):
    """A terminal color represented without renderer-specific escape codes."""

    kind: Literal["default", "indexed", "rgb"]
    index: int | None = Field(default=None, ge=0, le=255)
    red: int | None = Field(default=None, ge=0, le=255)
    green: int | None = Field(default=None, ge=0, le=255)
    blue: int | None = Field(default=None, ge=0, le=255)

    @model_validator(mode="after")
    def _validate_shape(self) -> TerminalColor:
        if self.kind == "default" and any(
            value is not None for value in (self.index, self.red, self.green, self.blue)
        ):
            raise ValueError("default terminal colors cannot have a value")
        if self.kind == "indexed" and (
            self.index is None
            or any(value is not None for value in (self.red, self.green, self.blue))
        ):
            raise ValueError("indexed terminal colors require only index")
        if self.kind == "rgb" and (
            self.index is not None
            or any(value is None for value in (self.red, self.green, self.blue))
        ):
            raise ValueError("rgb terminal colors require red, green, and blue")
        return self


class TerminalRendition(ApplicationModel):
    """Style attached to an individual terminal cell."""

    foreground: TerminalColor
    background: TerminalColor
    bold: bool = False
    faint: bool = False
    italic: bool = False
    underline: bool = False
    blink: bool = False
    inverse: bool = False
    invisible: bool = False
    strikethrough: bool = False


class TerminalCell(ApplicationModel):
    """One cell in the row-major grid of a terminal keyframe.

    Wide glyph continuations use ``width=0`` and an empty ``text``.  This lets
    a renderer reconstruct a grid without guessing from Unicode width rules.
    """

    text: str
    width: Literal[0, 1, 2]
    rendition: TerminalRendition

    @model_validator(mode="after")
    def _validate_width(self) -> TerminalCell:
        if self.width == 0 and self.text:
            raise ValueError("continuation cells must have empty text")
        if self.width > 0 and not self.text:
            raise ValueError("visible cells must have text")
        return self


class TerminalCursor(ApplicationModel):
    """Cursor position and shape in a complete terminal keyframe."""

    column: int = Field(ge=0)
    row: int = Field(ge=0)
    visible: bool
    shape: Literal["block", "underline", "bar"]


class TerminalModes(ApplicationModel):
    """DEC modes which affect how a terminal renderer accepts subsequent bytes."""

    application_cursor: bool
    application_keypad: bool
    bracketed_paste: bool
    insert: bool
    origin: bool
    wraparound: bool
    synchronized_updates: bool


class TerminalBuffer(ApplicationModel):
    """Complete state for one of the primary or alternate screen buffers."""

    cells: list[TerminalCell]
    cursor: TerminalCursor
    saved_cursor: TerminalCursor
    rendition: TerminalRendition
    saved_rendition: TerminalRendition
    scroll_top: int = Field(ge=0)
    scroll_bottom: int = Field(ge=0)
    wrap_pending: bool


class TerminalKeyframe(ApplicationModel):
    """Authoritative complete state that replaces every earlier terminal state.

    Both buffers are included even when the alternate one is active.  This is
    essential for a client which has to restore the primary buffer when a
    full-screen application later leaves the alternate buffer.
    """

    type: Literal["terminal.keyframe"] = "terminal.keyframe"
    sequence: int = Field(ge=1)
    captured_at: AwareDatetime
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)
    primary: TerminalBuffer
    alternate: TerminalBuffer
    active_buffer: Literal["primary", "alternate"]
    rendition: TerminalRendition
    modes: TerminalModes

    @model_validator(mode="after")
    def _validate_grid(self) -> TerminalKeyframe:
        expected_cells = self.columns * self.rows
        for name, buffer in (("primary", self.primary), ("alternate", self.alternate)):
            if len(buffer.cells) != expected_cells:
                raise ValueError(f"{name} cells must contain exactly columns * rows cells")
            for cursor_name, cursor in (
                ("cursor", buffer.cursor),
                ("saved_cursor", buffer.saved_cursor),
            ):
                if cursor.column >= self.columns or cursor.row >= self.rows:
                    raise ValueError(f"{name} {cursor_name} must be within terminal dimensions")
            if buffer.scroll_top > buffer.scroll_bottom or buffer.scroll_bottom >= self.rows:
                raise ValueError(f"{name} scroll region must be within terminal rows")
        return self


class TerminalFrame(ApplicationModel):
    """Legacy UTF-8 replace frame retained for compatibility-only captures.

    New streams use :class:`TerminalKeyframe`. Clients should treat this model
    as a replace update and never mix it with raw terminal chunks.
    """

    type: Literal["terminal.frame"] = "terminal.frame"
    subscription_id: str
    sequence: int = Field(ge=1)
    session_id: UUID
    captured_at: AwareDatetime
    columns: int = Field(ge=1)
    rows: int = Field(ge=1)
    encoding: Literal["utf-8"] = "utf-8"
    data: str
    reset: bool = True


class TerminalChunk(ApplicationModel):
    """Opaque raw terminal bytes following a keyframe or prior chunk.

    ``data`` is base64 so the JSON boundary preserves every byte exactly.
    Consumers must process it with their terminal parser rather than decode it
    as ordinary UTF-8 application text.
    """

    type: Literal["terminal.chunk"] = "terminal.chunk"
    sequence: int = Field(ge=1)
    encoding: Literal["base64"] = "base64"
    data: str = Field(min_length=1)


class TerminalStreamGap(ApplicationModel):
    """Explicitly reports a missing terminal sequence range."""

    type: Literal["terminal.gap"] = "terminal.gap"
    expected_sequence: int = Field(ge=1)
    next_sequence: int = Field(ge=1)

    @model_validator(mode="after")
    def _validate_range(self) -> TerminalStreamGap:
        if self.next_sequence <= self.expected_sequence:
            raise ValueError("next_sequence must follow expected_sequence")
        return self


__all__ = [
    "TerminalCell",
    "TerminalChunk",
    "TerminalColor",
    "TerminalCursor",
    "TerminalFrame",
    "TerminalKeyframe",
    "TerminalBuffer",
    "TerminalModes",
    "TerminalRendition",
    "TerminalStreamGap",
    "TerminalTarget",
]
