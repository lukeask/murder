"""A small, persistent VT screen emulator for terminal stream keyframes.

It intentionally consumes bytes, rather than decoded lines.  tmux's output is
an ordered byte stream and escape sequences (and UTF-8 characters) can span
read boundaries.  The emulator is therefore also useful when a client joins a
stream late: a keyframe is a faithful screen state, not a re-interpretation of
``capture-pane`` text.
"""

# VT/SGR dispatch is inherently a table of standardized numeric codes.
# Keeping those codes next to their behavior is clearer than indirection.
# ruff: noqa: PLR0912, PLR0915, PLR2004

from __future__ import annotations

import codecs
import unicodedata
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class VtRendition:
    """The portable subset of SGR state needed to reproduce a cell."""

    bold: bool = False
    faint: bool = False
    italic: bool = False
    underline: bool = False
    blink: bool = False
    inverse: bool = False
    invisible: bool = False
    strike: bool = False
    foreground: int | tuple[int, int, int] | None = None
    background: int | tuple[int, int, int] | None = None


DEFAULT_RENDITION = VtRendition()


@dataclass(frozen=True)
class VtCell:
    text: str = " "
    width: int = 1
    rendition: VtRendition = DEFAULT_RENDITION


@dataclass(frozen=True)
class VtCursor:
    x: int
    y: int
    visible: bool
    shape: str


@dataclass(frozen=True)
class VtModes:
    application_cursor: bool
    application_keypad: bool
    bracketed_paste: bool
    insert: bool
    origin: bool
    wraparound: bool
    synchronized_updates: bool


@dataclass(frozen=True)
class VtBufferSnapshot:
    cells: tuple[tuple[VtCell, ...], ...]
    cursor: VtCursor
    saved_cursor: VtCursor
    saved_rendition: VtRendition
    rendition: VtRendition
    scroll_top: int
    scroll_bottom: int
    wrap_pending: bool


@dataclass(frozen=True)
class VtSnapshot:
    columns: int
    rows: int
    primary: VtBufferSnapshot
    alternate: VtBufferSnapshot
    active_buffer: str
    modes: VtModes


class VtEmulator:
    """Practical ECMA-48/xterm screen model with primary and alternate buffers.

    This deliberately has no resize operation: tmux harness geometry is owned
    at session creation (normally 220x50), and observing output must never
    mutate it.  A new source replaces the emulator if dimensions genuinely
    change outside this code.
    """

    def __init__(self, columns: int, rows: int) -> None:
        if columns < 1 or rows < 1:
            raise ValueError("terminal dimensions must be positive")
        self.columns = columns
        self.rows = rows
        self._primary = self._blank_screen()
        self._alternate = self._blank_screen()
        self._screen = self._primary
        self._using_alternate = False
        self.x = 0
        self.y = 0
        self.saved_x = 0
        self.saved_y = 0
        self.saved_rendition = DEFAULT_RENDITION
        self.rendition = DEFAULT_RENDITION
        self.scroll_top = 0
        self.scroll_bottom = rows - 1
        self.cursor_visible = True
        self.cursor_shape = "block"
        self.application_cursor = False
        self.application_keypad = False
        self.bracketed_paste = False
        self.insert_mode = False
        self.origin_mode = False
        self.wraparound = True
        self.synchronized_updates = False
        self._pending_wrap = False
        self._state = "text"
        self._csi = bytearray()
        self._string_esc = False
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._primary_state = self._current_buffer_state()
        self._alternate_state = self._current_buffer_state(cells=self._alternate)

    def _blank_line(self) -> list[VtCell]:
        return [VtCell() for _ in range(self.columns)]

    def _blank_screen(self) -> list[list[VtCell]]:
        return [self._blank_line() for _ in range(self.rows)]

    def feed(self, data: bytes) -> None:
        """Feed raw tmux bytes. Partial UTF-8 and VT sequences are retained."""

        for byte in data:
            self._feed_byte(byte)

    def snapshot(self) -> VtSnapshot:
        self._store_active_state()
        return VtSnapshot(
            columns=self.columns,
            rows=self.rows,
            primary=self._primary_state,
            alternate=self._alternate_state,
            active_buffer="alternate" if self._using_alternate else "primary",
            modes=VtModes(
                application_cursor=self.application_cursor,
                application_keypad=self.application_keypad,
                bracketed_paste=self.bracketed_paste,
                insert=self.insert_mode,
                origin=self.origin_mode,
                wraparound=self.wraparound,
                synchronized_updates=self.synchronized_updates,
            ),
        )

    def _current_buffer_state(self, *, cells: list[list[VtCell]] | None = None) -> VtBufferSnapshot:
        return VtBufferSnapshot(
            cells=tuple(tuple(row) for row in (self._screen if cells is None else cells)),
            cursor=VtCursor(self.x, self.y, self.cursor_visible, self.cursor_shape),
            saved_cursor=VtCursor(
                self.saved_x, self.saved_y, self.cursor_visible, self.cursor_shape
            ),
            saved_rendition=self.saved_rendition,
            rendition=self.rendition,
            scroll_top=self.scroll_top,
            scroll_bottom=self.scroll_bottom,
            wrap_pending=self._pending_wrap,
        )

    def _store_active_state(self) -> None:
        state = self._current_buffer_state()
        if self._using_alternate:
            self._alternate_state = state
        else:
            self._primary_state = state

    def _restore_buffer_state(self, state: VtBufferSnapshot, *, alternate: bool) -> None:
        screen = [list(row) for row in state.cells]
        if alternate:
            self._alternate = screen
        else:
            self._primary = screen
        self._screen = screen
        self.x, self.y = state.cursor.x, state.cursor.y
        self.cursor_visible, self.cursor_shape = state.cursor.visible, state.cursor.shape
        self.saved_x, self.saved_y = state.saved_cursor.x, state.saved_cursor.y
        self.saved_rendition = state.saved_rendition
        self.rendition = state.rendition
        self.scroll_top, self.scroll_bottom = state.scroll_top, state.scroll_bottom
        self._pending_wrap = state.wrap_pending

    def _feed_byte(self, byte: int) -> None:
        if self._state == "string":
            if byte == 0x07:
                # OSC commonly terminates with BEL rather than ST.
                self._state = "text"
                self._string_esc = False
            elif self._string_esc and byte == 0x5C:
                self._state = "text"
                self._string_esc = False
            else:
                self._string_esc = byte == 0x1B
            return
        if self._state == "esc":
            self._escape(byte)
            return
        if self._state == "csi":
            if 0x40 <= byte <= 0x7E:
                self._csi.append(byte)
                self._csi_dispatch(bytes(self._csi))
                self._csi.clear()
                self._state = "text"
            elif len(self._csi) < 4096:
                self._csi.append(byte)
            else:
                self._csi.clear()
                self._state = "text"
            return
        if byte == 0x1B:
            self._flush_text()
            self._state = "esc"
        elif byte < 0x20 or byte == 0x7F:
            self._flush_text()
            self._control(byte)
        else:
            text = self._decoder.decode(bytes((byte,)), final=False)
            for char in text:
                self._put_char(char)

    def _flush_text(self) -> None:
        text = self._decoder.decode(b"", final=False)
        for char in text:
            self._put_char(char)

    def _escape(self, byte: int) -> None:
        self._state = "text"
        if byte == ord("["):
            self._state = "csi"
        elif byte in (ord("]"), ord("P"), ord("^"), ord("_")):
            self._state = "string"
            self._string_esc = False
        elif byte == ord("7"):
            self._save_cursor()
        elif byte == ord("8"):
            self._restore_cursor()
        elif byte == ord("D"):
            self._index()
        elif byte == ord("M"):
            self._reverse_index()
        elif byte == ord("E"):
            self._index()
            self.x = 0
        elif byte == ord("H"):
            # Horizontal tab stop is not externally visible in a keyframe.
            # default tab stops remain every eight columns.
            return
        elif byte == ord("="):
            self.application_keypad = True
        elif byte == ord(">"):
            self.application_keypad = False
        elif byte == ord("c"):
            self._reset()

    def _control(self, byte: int) -> None:
        if byte == 0x08:  # BS
            self.x = max(0, self.x - 1)
            self._pending_wrap = False
        elif byte == 0x09:  # HT
            self.x = min(self.columns - 1, ((self.x // 8) + 1) * 8)
            self._pending_wrap = False
        elif byte in (0x0A, 0x0B, 0x0C):
            self._index()
        elif byte == 0x0D:
            self.x = 0
            self._pending_wrap = False

    def _csi_dispatch(self, sequence: bytes) -> None:
        final = chr(sequence[-1])
        body = sequence[:-1].decode("ascii", "ignore")
        cursor_style = final == "q" and body.endswith(" ")
        if cursor_style:
            body = body[:-1]
        private = body.startswith("?")
        if private:
            body = body[1:]
        parts = body.split(";") if body else []
        params = [int(part) if part.isdigit() else 0 for part in parts]

        def one(default: int = 1) -> int:
            return params[0] if params and params[0] else default

        self._pending_wrap = False
        if final == "A":
            self.y = max(self._home_top(), self.y - one())
        elif final == "B":
            self.y = min(self._home_bottom(), self.y + one())
        elif final == "C":
            self.x = min(self.columns - 1, self.x + one())
        elif final == "D":
            self.x = max(0, self.x - one())
        elif final == "E":
            self.y = min(self._home_bottom(), self.y + one())
            self.x = 0
        elif final == "F":
            self.y = max(self._home_top(), self.y - one())
            self.x = 0
        elif final in ("G", "`"):
            self.x = min(self.columns - 1, max(0, one() - 1))
        elif final in ("H", "f"):
            row = (params[0] if params and params[0] else 1) - 1
            col = (params[1] if len(params) > 1 and params[1] else 1) - 1
            self.y = min(
                self._home_bottom(),
                max(self._home_top(), self._home_top() + row if self.origin_mode else row),
            )
            self.x = min(self.columns - 1, max(0, col))
        elif final == "d":
            row = one() - 1
            self.y = min(
                self._home_bottom(),
                max(self._home_top(), self._home_top() + row if self.origin_mode else row),
            )
        elif final == "J":
            self._erase_display(params[0] if params else 0)
        elif final == "K":
            self._erase_line(params[0] if params else 0)
        elif final == "X":
            self._erase_chars(one())
        elif final == "@":
            self._insert_chars(one())
        elif final == "P":
            self._delete_chars(one())
        elif final == "L":
            self._insert_lines(one())
        elif final == "M":
            self._delete_lines(one())
        elif final == "S":
            self._scroll_up(one())
        elif final == "T":
            self._scroll_down(one())
        elif final == "r":
            top = (params[0] if params and params[0] else 1) - 1
            bottom = (params[1] if len(params) > 1 and params[1] else self.rows) - 1
            if 0 <= top < bottom < self.rows:
                self.scroll_top, self.scroll_bottom = top, bottom
            self.x = 0
            self.y = self._home_top()
        elif final == "m":
            self._sgr(params or [0])
        elif final == "h":
            self._set_modes(params, private, True)
        elif final == "l":
            self._set_modes(params, private, False)
        elif final == "s":
            self._save_cursor()
        elif final == "u":
            self._restore_cursor()
        elif cursor_style:
            self.cursor_shape = {
                1: "block",
                2: "block",
                3: "underline",
                4: "underline",
                5: "bar",
                6: "bar",
            }.get(one(), "block")

    def _set_modes(self, params: list[int], private: bool, enabled: bool) -> None:
        for param in params:
            if private:
                if param == 1:
                    self.application_cursor = enabled
                elif param == 6:
                    self.origin_mode = enabled
                    self.x, self.y = 0, self._home_top()
                elif param == 7:
                    self.wraparound = enabled
                elif param == 25:
                    self.cursor_visible = enabled
                elif param in (47, 1047, 1049):
                    if enabled:
                        if param == 1049:
                            self._save_cursor()
                        self._enter_alternate()
                    else:
                        self._leave_alternate()
                        if param == 1049:
                            self._restore_cursor()
                elif param == 2004:
                    self.bracketed_paste = enabled
                elif param == 2026:
                    self.synchronized_updates = enabled
            elif param == 4:
                self.insert_mode = enabled

    def _enter_alternate(self) -> None:
        if not self._using_alternate:
            self._store_active_state()
            self._restore_buffer_state(self._alternate_state, alternate=True)
            self._using_alternate = True

    def _leave_alternate(self) -> None:
        if self._using_alternate:
            self._store_active_state()
            self._restore_buffer_state(self._primary_state, alternate=False)
            self._using_alternate = False

    def _put_char(self, char: str) -> None:
        if not char:
            return
        if unicodedata.combining(char):
            if self.x > 0:
                idx = self.x - 1
                if self._screen[self.y][idx].width == 0 and idx > 0:
                    idx -= 1
                cell = self._screen[self.y][idx]
                self._screen[self.y][idx] = replace(cell, text=cell.text + char)
            return
        width = 2 if _is_wide(char) else 1
        if self._pending_wrap:
            self.x = 0
            self._index()
            self._pending_wrap = False
        if width == 2 and self.x == self.columns - 1:
            if self.wraparound:
                self.x = 0
                self._index()
            else:
                return
        if self.insert_mode:
            self._insert_chars(width)
        self._clear_overlap(self.y, self.x)
        if width == 2:
            self._clear_overlap(self.y, self.x + 1)
        self._screen[self.y][self.x] = VtCell(char, width, self.rendition)
        if width == 2:
            self._screen[self.y][self.x + 1] = VtCell("", 0, self.rendition)
        self.x += width
        if self.x >= self.columns:
            self.x = self.columns - 1
            self._pending_wrap = self.wraparound

    def _clear_overlap(self, y: int, x: int) -> None:
        if x < 0 or x >= self.columns:
            return
        if self._screen[y][x].width == 0 and x > 0:
            self._screen[y][x - 1] = VtCell()
        if self._screen[y][x].width == 2 and x + 1 < self.columns:
            self._screen[y][x + 1] = VtCell()

    def _repair_wide_cells(self, row: list[VtCell]) -> None:
        x = 0
        while x < self.columns:
            cell = row[x]
            if cell.width == 0:
                if x == 0 or row[x - 1].width != 2:
                    row[x] = VtCell()
            elif cell.width == 2:
                if x + 1 >= self.columns:
                    row[x] = VtCell()
                else:
                    row[x + 1] = VtCell("", 0, cell.rendition)
                    x += 1
            x += 1

    def _index(self) -> None:
        if self.y == self.scroll_bottom:
            self._scroll_up(1)
        else:
            self.y = min(self.rows - 1, self.y + 1)
        self._pending_wrap = False

    def _reverse_index(self) -> None:
        if self.y == self.scroll_top:
            self._scroll_down(1)
        else:
            self.y = max(0, self.y - 1)

    def _scroll_up(self, count: int) -> None:
        count = min(max(1, count), self.scroll_bottom - self.scroll_top + 1)
        region = self._screen[self.scroll_top : self.scroll_bottom + 1]
        self._screen[self.scroll_top : self.scroll_bottom + 1] = region[count:] + [
            self._blank_line() for _ in range(count)
        ]

    def _scroll_down(self, count: int) -> None:
        count = min(max(1, count), self.scroll_bottom - self.scroll_top + 1)
        region = self._screen[self.scroll_top : self.scroll_bottom + 1]
        self._screen[self.scroll_top : self.scroll_bottom + 1] = [
            self._blank_line() for _ in range(count)
        ] + region[:-count]

    def _erase_display(self, mode: int) -> None:
        if mode in (2, 3):
            for row in range(self.rows):
                self._screen[row] = self._blank_line()
        elif mode == 1:
            for row in range(self.y):
                self._screen[row] = self._blank_line()
            self._clear_overlap(self.y, self.x)
            self._screen[self.y][: self.x + 1] = [VtCell() for _ in range(self.x + 1)]
            self._repair_wide_cells(self._screen[self.y])
        else:
            self._clear_overlap(self.y, self.x)
            self._screen[self.y][self.x :] = [VtCell() for _ in range(self.columns - self.x)]
            self._repair_wide_cells(self._screen[self.y])
            for row in range(self.y + 1, self.rows):
                self._screen[row] = self._blank_line()

    def _erase_line(self, mode: int) -> None:
        if mode == 1:
            start, end = 0, self.x + 1
        elif mode == 2:
            start, end = 0, self.columns
        else:
            start, end = self.x, self.columns
        self._clear_overlap(self.y, start)
        self._clear_overlap(self.y, end - 1)
        self._screen[self.y][start:end] = [VtCell() for _ in range(end - start)]
        self._repair_wide_cells(self._screen[self.y])

    def _erase_chars(self, count: int) -> None:
        end = min(self.columns, self.x + count)
        self._clear_overlap(self.y, self.x)
        self._clear_overlap(self.y, end - 1)
        self._screen[self.y][self.x : end] = [VtCell() for _ in range(end - self.x)]
        self._repair_wide_cells(self._screen[self.y])

    def _insert_chars(self, count: int) -> None:
        count = min(count, self.columns - self.x)
        row = self._screen[self.y]
        self._clear_overlap(self.y, self.x)
        row[self.x :] = [VtCell() for _ in range(count)] + row[self.x : self.columns - count]
        self._repair_wide_cells(row)

    def _delete_chars(self, count: int) -> None:
        count = min(count, self.columns - self.x)
        row = self._screen[self.y]
        self._clear_overlap(self.y, self.x)
        self._clear_overlap(self.y, self.x + count)
        row[self.x :] = row[self.x + count :] + [VtCell() for _ in range(count)]
        self._repair_wide_cells(row)

    def _insert_lines(self, count: int) -> None:
        if not self.scroll_top <= self.y <= self.scroll_bottom:
            return
        count = min(count, self.scroll_bottom - self.y + 1)
        region = self._screen[self.y : self.scroll_bottom + 1]
        self._screen[self.y : self.scroll_bottom + 1] = [
            self._blank_line() for _ in range(count)
        ] + region[:-count]

    def _delete_lines(self, count: int) -> None:
        if not self.scroll_top <= self.y <= self.scroll_bottom:
            return
        count = min(count, self.scroll_bottom - self.y + 1)
        region = self._screen[self.y : self.scroll_bottom + 1]
        self._screen[self.y : self.scroll_bottom + 1] = region[count:] + [
            self._blank_line() for _ in range(count)
        ]

    def _sgr(self, params: list[int]) -> None:
        i = 0
        value = self.rendition
        while i < len(params):
            p = params[i]
            if p == 0:
                value = DEFAULT_RENDITION
            elif p == 1:
                value = replace(value, bold=True)
            elif p == 2:
                value = replace(value, faint=True)
            elif p == 3:
                value = replace(value, italic=True)
            elif p == 4:
                value = replace(value, underline=True)
            elif p == 5:
                value = replace(value, blink=True)
            elif p == 7:
                value = replace(value, inverse=True)
            elif p == 8:
                value = replace(value, invisible=True)
            elif p == 9:
                value = replace(value, strike=True)
            elif p == 22:
                value = replace(value, bold=False, faint=False)
            elif p == 23:
                value = replace(value, italic=False)
            elif p == 24:
                value = replace(value, underline=False)
            elif p == 25:
                value = replace(value, blink=False)
            elif p == 27:
                value = replace(value, inverse=False)
            elif p == 28:
                value = replace(value, invisible=False)
            elif p == 29:
                value = replace(value, strike=False)
            elif 30 <= p <= 37 or 90 <= p <= 97:
                value = replace(value, foreground=p - 30 if p < 90 else p - 82)
            elif p == 39:
                value = replace(value, foreground=None)
            elif 40 <= p <= 47 or 100 <= p <= 107:
                value = replace(value, background=p - 40 if p < 100 else p - 92)
            elif p == 49:
                value = replace(value, background=None)
            elif p in (38, 48) and i + 1 < len(params):
                target = "foreground" if p == 38 else "background"
                if params[i + 1] == 5 and i + 2 < len(params):
                    if target == "foreground":
                        value = replace(value, foreground=params[i + 2])
                    else:
                        value = replace(value, background=params[i + 2])
                    i += 2
                elif params[i + 1] == 2 and i + 4 < len(params):
                    rgb = tuple(max(0, min(255, item)) for item in params[i + 2 : i + 5])
                    rgb_value = (rgb[0], rgb[1], rgb[2])
                    if target == "foreground":
                        value = replace(value, foreground=rgb_value)
                    else:
                        value = replace(value, background=rgb_value)
                    i += 4
            i += 1
        self.rendition = value

    def _save_cursor(self) -> None:
        self.saved_x, self.saved_y, self.saved_rendition = self.x, self.y, self.rendition

    def _restore_cursor(self) -> None:
        self.x, self.y, self.rendition = self.saved_x, self.saved_y, self.saved_rendition
        self.x = min(max(0, self.x), self.columns - 1)
        self.y = min(max(0, self.y), self.rows - 1)

    def _reset(self) -> None:
        self.rendition = DEFAULT_RENDITION
        self.scroll_top, self.scroll_bottom = 0, self.rows - 1
        self.x = self.y = 0
        self.origin_mode = self.insert_mode = self.application_cursor = self.application_keypad = (
            False
        )
        self.bracketed_paste = False
        self.synchronized_updates = False
        self.wraparound = self.cursor_visible = True
        self._pending_wrap = False
        self._leave_alternate()

    def _home_top(self) -> int:
        return self.scroll_top if self.origin_mode else 0

    def _home_bottom(self) -> int:
        return self.scroll_bottom if self.origin_mode else self.rows - 1


def _is_wide(char: str) -> bool:
    return unicodedata.east_asian_width(char) in {"W", "F"} or ord(char) >= 0x1F300


__all__ = [
    "VtBufferSnapshot",
    "VtCell",
    "VtCursor",
    "VtEmulator",
    "VtModes",
    "VtRendition",
    "VtSnapshot",
]
