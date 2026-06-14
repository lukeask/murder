/**
 * `chatBuffer` — the pure cursor model + edit ops + visual-wrap navigation for the chat input.
 *
 * This is the framework-agnostic heart of the chat-input overhaul (WS-A). It owns NOTHING stateful:
 * every operation takes a {@link BufferState} (immutable `{ text, cursor }`) and returns a new one.
 * The zustand store ({@link ./chatInputStore.js}, owned by WS-E) holds a single live `BufferState`
 * and routes keystrokes through these functions; the vim reducer (WS-B) drives the same ops; the
 * `ChatInput` component (WS-E) renders via {@link layout}. No React, no Ink, no zustand here.
 *
 * ## The cursor
 *
 * `cursor` is a **character offset** into `text`, in `[0, text.length]`. It is ALWAYS on a span
 * boundary — it never lands strictly inside a marked image span `U+E000<id>U+E001`. Edits and motion
 * snap the cursor over a whole span as one atomic unit (so backspace at a span's trailing edge eats
 * the entire span and returns its id, left-arrow at a span's trailing edge jumps to its leading edge,
 * etc.). The span format is shared with {@link ./chatInputStore.js} — we import its constants /
 * regex-derivation so the two stay in lockstep (the buffer holds image *ids*, never the visible
 * `[Image N]` label, which is positional and derived at render).
 *
 * ## Display mapping (image spans → `[Image N]`)
 *
 * The chat box wraps the **display** text, in which each `U+E000<id>U+E001` span is shown as
 * `[Image N]` (N = 1-based positional count). Because the display string differs in length from the
 * buffer string whenever spans are present, {@link layout} / {@link visualUp} / {@link visualDown}
 * work over a {@link DisplayMap}: the rendered string plus a per-display-char array of source buffer
 * offsets. All three report **buffer** offsets (`startBufferOffset`, the cursor mapping) so the caller
 * never has to know about the display↔buffer skew. The common path — plain text, no spans — produces
 * an identity map and a `display === text`, kept correct and cheap.
 *
 * ## Wrap rule (we OWN it — render and nav MUST agree)
 *
 * `layout(s, width)` wraps the display text at `width` content cells (`width >= 1`):
 *  - hard-break on every `'\n'` (the newline itself does not occupy a cell; it ends a row);
 *  - otherwise soft-wrap by **word** (break before a word that would overflow the current row);
 *  - a single word longer than `width` is hard-broken at the width boundary;
 *  - a trailing cursor sitting at exactly `width` on an otherwise-full row lands on a fresh next row
 *    (so the block cursor is always drawable).
 *
 * `measureElement` is unreliable for wrapped flex text (memory: it measures 1 row, draws 2), so we do
 * NOT lean on Ink's measurement — the layout computed here is authoritative for both render and nav.
 */

import { SPAN_CLOSE, SPAN_OPEN } from './chatInputStore.js';

/**
 * Buffer + cursor. `cursor` is a CHARACTER offset into `text` in `[0, text.length]`, and is always on
 * a span boundary (never inside `U+E000…U+E001`). Immutable — edit ops return a fresh state.
 */
export interface BufferState {
  readonly text: string;
  readonly cursor: number;
}

/** The canonical empty buffer: no text, cursor at 0. */
export const EMPTY_BUFFER: BufferState = { text: '', cursor: 0 };

// --------------------------------------------------------------------------------------------------
// Span scanning — a flat list of every marked image span in the buffer, in order.
// --------------------------------------------------------------------------------------------------

/** One marked image span found in the buffer: its id and its `[start, end)` char range in `text`. */
interface Span {
  readonly id: string;
  readonly start: number;
  readonly end: number;
}

/**
 * Scan `text` for marked image spans, returning each one's id + `[start, end)` range, in order.
 * A span is `U+E000` … next `U+E001`; the id is everything between (ids never contain PUA delimiters).
 * An unterminated `U+E000` (no closing delimiter) is ignored — there is no atomic span to protect.
 */
function scanSpans(text: string): readonly Span[] {
  const spans: Span[] = [];
  let from = 0;
  while (true) {
    const open = text.indexOf(SPAN_OPEN, from);
    if (open === -1) {
      break;
    }
    const close = text.indexOf(SPAN_CLOSE, open + 1);
    if (close === -1) {
      break;
    }
    spans.push({ id: text.slice(open + 1, close), start: open, end: close + 1 });
    from = close + 1;
  }
  return spans;
}

/** The span whose interior strictly contains `offset` (i.e. `start < offset < end`), or null. Used to
 * snap a cursor that would otherwise land inside a span out to one of its edges. */
function spanContaining(spans: readonly Span[], offset: number): Span | null {
  for (const span of spans) {
    if (offset > span.start && offset < span.end) {
      return span;
    }
  }
  return null;
}

/** The span that ends exactly at `offset` (its trailing edge), or null. */
function spanEndingAt(spans: readonly Span[], offset: number): Span | null {
  for (const span of spans) {
    if (span.end === offset) {
      return span;
    }
  }
  return null;
}

/** The span that starts exactly at `offset` (its leading edge), or null. */
function spanStartingAt(spans: readonly Span[], offset: number): Span | null {
  for (const span of spans) {
    if (span.start === offset) {
      return span;
    }
  }
  return null;
}

/** Clamp + snap an offset to the nearest valid cursor position: inside `[0, len]` and never strictly
 * inside a span (snapped to the span's leading edge if it would land in the interior). */
function snap(text: string, offset: number, spans: readonly Span[]): number {
  const clamped = offset < 0 ? 0 : offset > text.length ? text.length : offset;
  const inside = spanContaining(spans, clamped);
  return inside === null ? clamped : inside.start;
}

/**
 * Public span-snap for offsets computed OUTSIDE the chatBuffer motion ops (e.g. the vim reducer's
 * logical line up/down and paste, which build a target offset by raw arithmetic). Clamps `offset` to
 * `[0, text.length]` and pulls it to the span's leading edge if it would land strictly inside an image
 * span, preserving the boundary invariant. Scans spans itself so callers need no span knowledge.
 */
export function snapCursor(text: string, offset: number): number {
  return snap(text, offset, scanSpans(text));
}

// --------------------------------------------------------------------------------------------------
// Edit ops
// --------------------------------------------------------------------------------------------------

/** Insert `str` at the cursor, advancing the cursor past the inserted text. */
export function insert(s: BufferState, str: string): BufferState {
  const text = s.text.slice(0, s.cursor) + str + s.text.slice(s.cursor);
  return { text, cursor: s.cursor + str.length };
}

/** Insert an atomic marked image span carrying `id` at the cursor; cursor lands after the span. */
export function insertImageSpan(s: BufferState, id: string): BufferState {
  return insert(s, `${SPAN_OPEN}${id}${SPAN_CLOSE}`);
}

/**
 * Delete the char/span immediately BEFORE the cursor (Backspace). If the cursor sits at a span's
 * trailing edge, the WHOLE span is removed and its id returned (so the handler can drop the matching
 * imageDraft entry). Otherwise one char is removed. No-op (returns `removedId: null`) at offset 0.
 */
export function backspace(s: BufferState): { state: BufferState; removedId: string | null } {
  if (s.cursor === 0) {
    return { state: s, removedId: null };
  }
  const spans = scanSpans(s.text);
  const span = spanEndingAt(spans, s.cursor);
  if (span !== null) {
    const text = s.text.slice(0, span.start) + s.text.slice(span.end);
    return { state: { text, cursor: span.start }, removedId: span.id };
  }
  const text = s.text.slice(0, s.cursor - 1) + s.text.slice(s.cursor);
  return { state: { text, cursor: s.cursor - 1 }, removedId: null };
}

/**
 * Delete the char/span AT the cursor (Delete key / vim `x`). If the cursor sits at a span's leading
 * edge, the WHOLE span is removed and its id returned. Otherwise one char is removed. The cursor does
 * not move. No-op (returns `removedId: null`) at the buffer end.
 */
export function deleteForward(s: BufferState): { state: BufferState; removedId: string | null } {
  if (s.cursor >= s.text.length) {
    return { state: s, removedId: null };
  }
  const spans = scanSpans(s.text);
  const span = spanStartingAt(spans, s.cursor);
  if (span !== null) {
    const text = s.text.slice(0, span.start) + s.text.slice(span.end);
    return { state: { text, cursor: span.start }, removedId: span.id };
  }
  const text = s.text.slice(0, s.cursor) + s.text.slice(s.cursor + 1);
  return { state: { text, cursor: s.cursor }, removedId: null };
}

// --------------------------------------------------------------------------------------------------
// Horizontal motion (char-wise, snapping over whole spans)
// --------------------------------------------------------------------------------------------------

/** Move one char left; at a span's trailing edge, jump to its leading edge (skip the whole span). */
export function moveLeft(s: BufferState): BufferState {
  if (s.cursor === 0) {
    return s;
  }
  const spans = scanSpans(s.text);
  const span = spanEndingAt(spans, s.cursor);
  return { text: s.text, cursor: span !== null ? span.start : s.cursor - 1 };
}

/** Move one char right; at a span's leading edge, jump to its trailing edge (skip the whole span). */
export function moveRight(s: BufferState): BufferState {
  if (s.cursor >= s.text.length) {
    return s;
  }
  const spans = scanSpans(s.text);
  const span = spanStartingAt(spans, s.cursor);
  return { text: s.text, cursor: span !== null ? span.end : s.cursor + 1 };
}

/** Home: move to the start of the current LOGICAL line (just after the preceding `'\n'`, or 0). */
export function moveLineStart(s: BufferState): BufferState {
  const nl = s.text.lastIndexOf('\n', s.cursor - 1);
  return { text: s.text, cursor: nl === -1 ? 0 : nl + 1 };
}

/** End: move to the end of the current LOGICAL line (just before the next `'\n'`, or buffer end). */
export function moveLineEnd(s: BufferState): BufferState {
  const nl = s.text.indexOf('\n', s.cursor);
  return { text: s.text, cursor: nl === -1 ? s.text.length : nl };
}

/** Move to the very start of the buffer. */
export function moveBufferStart(s: BufferState): BufferState {
  return { text: s.text, cursor: 0 };
}

/** Move to the very end of the buffer. */
export function moveBufferEnd(s: BufferState): BufferState {
  return { text: s.text, cursor: s.text.length };
}

// --------------------------------------------------------------------------------------------------
// Vim word motion (whitespace-delimited; a "word" is a maximal run of non-space chars)
// --------------------------------------------------------------------------------------------------

/** True if `text[i]` exists and is whitespace. */
function isSpaceAt(text: string, i: number): boolean {
  const ch = text[i];
  return ch !== undefined && /\s/.test(ch);
}

/**
 * Vim `w`: move to the start of the next word. Skips the current word's remaining chars, then any
 * whitespace, landing on the first char of the following word (or buffer end if none). A span counts
 * as a non-space unit, so `w` from inside-the-current-word past a span lands after it on the next
 * word; the result is always span-snapped.
 */
export function moveWordForward(s: BufferState): BufferState {
  const { text } = s;
  let i = s.cursor;
  // Skip the rest of the current word (non-space run).
  while (i < text.length && !isSpaceAt(text, i)) {
    i++;
  }
  // Skip the whitespace gap.
  while (i < text.length && isSpaceAt(text, i)) {
    i++;
  }
  return { text, cursor: snap(text, i, scanSpans(text)) };
}

/**
 * Vim `b`: move to the start of the current or previous word. Skips whitespace to the left, then the
 * word's chars, landing on the word's first char (or 0). Span-snapped.
 */
export function moveWordBackward(s: BufferState): BufferState {
  const { text } = s;
  let i = s.cursor;
  // Step back over any whitespace immediately to the left.
  while (i > 0 && isSpaceAt(text, i - 1)) {
    i--;
  }
  // Step back over the word's chars to its first char.
  while (i > 0 && !isSpaceAt(text, i - 1)) {
    i--;
  }
  return { text, cursor: snap(text, i, scanSpans(text)) };
}

/**
 * Vim `e`: move to the END of the current or next word — the last char of the word, i.e. the offset
 * just before the first trailing space (or buffer end). Always advances at least one position so
 * repeated `e` walks word-ends. Span-snapped (lands on the span's trailing edge if the word ends in
 * a span).
 */
export function moveWordEnd(s: BufferState): BufferState {
  const { text } = s;
  let i = s.cursor + 1;
  // Skip leading whitespace.
  while (i < text.length && isSpaceAt(text, i)) {
    i++;
  }
  // Advance to just past the last char of this word (stop at the next space / at end).
  while (i < text.length && !isSpaceAt(text, i)) {
    i++;
  }
  // `e` lands ON the word's last char (the offset just before the trailing space / end).
  const end = i > s.cursor + 1 ? i - 1 : i;
  const clamped = end > text.length ? text.length : end;
  return { text, cursor: snap(text, clamped, scanSpans(text)) };
}

// --------------------------------------------------------------------------------------------------
// Display mapping (buffer ↔ rendered `[Image N]` string)
// --------------------------------------------------------------------------------------------------

/**
 * The rendered display string plus the buffer↔display index correspondence:
 *  - `display`: the text as shown (spans replaced by `[Image N]`);
 *  - `srcOffset[d]`: the BUFFER offset that display char `d` originates from. Length `display.length`.
 *  - `bufToDisplay(b)`: the display offset for buffer cursor offset `b` (b is on a span boundary).
 *  - `displayToBuf(d)`: the buffer offset for display offset `d` (snapped to a span boundary).
 */
interface DisplayMap {
  readonly display: string;
  readonly srcOffset: readonly number[];
  bufToDisplay(b: number): number;
  displayToBuf(d: number): number;
}

/** Build the {@link DisplayMap} for `text`. Plain text (no spans) takes the identity fast path. */
function buildDisplayMap(text: string): DisplayMap {
  const spans = scanSpans(text);
  if (spans.length === 0) {
    return {
      display: text,
      srcOffset: identityRange(text.length),
      bufToDisplay: (b) => b,
      displayToBuf: (d) => d,
    };
  }

  // Walk the buffer, copying plain chars verbatim and substituting `[Image N]` for each span. We track
  // every plain-char position and each span's leading/trailing edges so cursor offsets (always on a
  // boundary) map cleanly. `displayForBuf[b]` = display offset for buffer boundary b.
  let display = '';
  const srcOffset: number[] = [];
  const displayForBuf: number[] = new Array(text.length + 1).fill(-1);
  let b = 0;
  let n = 0;
  let nextSpan = 0;
  while (b <= text.length) {
    displayForBuf[b] = display.length;
    if (nextSpan < spans.length && spans[nextSpan]?.start === b) {
      const span = spans[nextSpan];
      if (span === undefined) {
        break;
      }
      n++;
      const label = `[Image ${n}]`;
      for (let k = 0; k < label.length; k++) {
        display += label[k];
        // Every display cell of the label maps back to the span's leading edge (its boundary).
        srcOffset.push(span.start);
      }
      // Advance the buffer cursor over the whole span; its trailing edge maps to the label's end.
      b = span.end;
      displayForBuf[b] = display.length;
      nextSpan++;
      continue;
    }
    if (b < text.length) {
      const ch = text[b];
      if (ch !== undefined) {
        display += ch;
        srcOffset.push(b);
      }
    }
    b++;
  }

  // Fill any still-unset buffer offsets (interior-of-span positions never used by a snapped cursor)
  // by carrying the previous display offset forward, so the map is total and monotonic.
  for (let i = 1; i < displayForBuf.length; i++) {
    if (displayForBuf[i] === -1) {
      displayForBuf[i] = displayForBuf[i - 1] ?? 0;
    }
  }

  return {
    display,
    srcOffset,
    bufToDisplay: (buf) => displayForBuf[buf] ?? display.length,
    displayToBuf: (d) => {
      if (d <= 0) {
        return 0;
      }
      if (d >= srcOffset.length) {
        return text.length;
      }
      // `srcOffset[d]` is the buffer offset of display char d; that is already a boundary (span chars
      // map to the span start). Snap defensively.
      return snap(text, srcOffset[d] ?? d, spans);
    },
  };
}

/** `[0, 1, …, n]` — the identity offset array of length `n` (used for the plain-text fast path). */
function identityRange(n: number): number[] {
  const out: number[] = new Array(n);
  for (let i = 0; i < n; i++) {
    out[i] = i;
  }
  return out;
}

// --------------------------------------------------------------------------------------------------
// Visual (soft-wrap-aware) layout + vertical motion
// --------------------------------------------------------------------------------------------------

/** One visual row: the display substring + the BUFFER offset its first glyph maps to. */
export interface VisualRow {
  readonly text: string;
  readonly startBufferOffset: number;
}

export interface VisualLayout {
  readonly rows: readonly VisualRow[];
  /** Index into `rows` where the cursor lands. */
  readonly cursorRow: number;
  /** Column within that row, in display cells. */
  readonly cursorCol: number;
}

/** A wrapped row described in DISPLAY-offset space: `[start, end)` into the display string. */
interface DisplayRow {
  readonly start: number;
  readonly end: number;
}

/**
 * Wrap `display` into rows at `width` content cells, following the locked wrap rule (hard-break on
 * `'\n'`, soft-wrap by word, hard-break an over-long word). Returns `[start, end)` ranges in DISPLAY
 * space. A `'\n'` ends the current row and is NOT included in either row's range. There is always at
 * least one row (the empty buffer → one empty row).
 */
function wrapDisplay(display: string, width: number): DisplayRow[] {
  const w = width < 1 ? 1 : Math.floor(width);
  const rows: DisplayRow[] = [];
  // Split into logical lines on '\n' first, recording each line's display range.
  let lineStart = 0;
  const logicalLines: DisplayRow[] = [];
  for (let i = 0; i <= display.length; i++) {
    if (i === display.length || display[i] === '\n') {
      logicalLines.push({ start: lineStart, end: i });
      lineStart = i + 1;
    }
  }

  for (const line of logicalLines) {
    let pos = line.start;
    if (pos === line.end) {
      // Empty logical line → one empty row.
      rows.push({ start: pos, end: pos });
      continue;
    }
    while (pos < line.end) {
      const remaining = line.end - pos;
      if (remaining <= w) {
        // The rest of the logical line fits in one row.
        rows.push({ start: pos, end: line.end });
        pos = line.end;
        break;
      }
      // The line overflows `w` from `pos`. Find the soft-break point: the last space at an index in
      // `(pos, pos+w]` (inclusive of the cell at the width boundary — a space sitting exactly at the
      // edge lets the preceding words fill the row). The space(s) are consumed at the seam.
      const hardLimit = pos + w;
      let breakSpace = -1;
      for (let i = hardLimit; i > pos; i--) {
        if (display[i] === ' ') {
          breakSpace = i;
          break;
        }
      }
      if (breakSpace === -1) {
        // No space to break on within the window → hard-break a too-long word at the width boundary.
        rows.push({ start: pos, end: hardLimit });
        pos = hardLimit;
        continue;
      }
      // End the row before the run of spaces ending at `breakSpace`; trim trailing spaces off the row.
      let rowEnd = breakSpace;
      while (rowEnd > pos && display[rowEnd - 1] === ' ') {
        rowEnd--;
      }
      rows.push({ start: pos, end: rowEnd });
      // Skip the space run to the next word start.
      let next = breakSpace + 1;
      while (next < line.end && display[next] === ' ') {
        next++;
      }
      pos = next;
    }
  }
  if (rows.length === 0) {
    rows.push({ start: 0, end: 0 });
  }
  return rows;
}

/**
 * Compute the wrapped layout + where the cursor lands, for a content width in cells (`width >= 1`).
 * Rows carry DISPLAY substrings but report BUFFER offsets via the display map; the cursor row/col are
 * located by mapping the buffer cursor into display space and finding its row.
 *
 * Trailing-cursor rule: when the cursor sits at exactly `width` on an otherwise-full soft-wrapped row,
 * it belongs on a fresh next row (so a block cursor is always drawable). We resolve this by choosing,
 * among the rows whose `[start, end]` span the cursor display offset, the LAST one whose start `<=`
 * the offset — and if the cursor is exactly at a row's `end` that is also the next row's `start`, we
 * prefer the next row.
 */
export function layout(s: BufferState, width: number): VisualLayout {
  const w = width < 1 ? 1 : Math.floor(width);
  const map = buildDisplayMap(s.text);
  const drows = wrapDisplay(map.display, w);
  const cursorDisp = map.bufToDisplay(s.cursor);

  const rows: VisualRow[] = drows.map((r) => ({
    text: map.display.slice(r.start, r.end),
    startBufferOffset: map.displayToBuf(r.start),
  }));

  // Trailing-cursor-at-width: if the cursor sits at exactly `w` cells into a FULL row (length === w)
  // and that row is NOT soft-continued by a following row (it's the last row, or the next row is a
  // separate logical line after a '\n'), the block cursor belongs on a fresh next row. Synthesize it
  // so render draws an empty continuation row with the cursor at its head.
  for (let i = 0; i < drows.length; i++) {
    const r = drows[i];
    if (r === undefined) {
      continue;
    }
    const full = r.end - r.start === w;
    if (!full || cursorDisp !== r.end) {
      continue;
    }
    const next = drows[i + 1];
    const softContinued = next !== undefined && next.start === r.end;
    if (!softContinued) {
      rows.splice(i + 1, 0, { text: '', startBufferOffset: map.displayToBuf(r.end) });
      return { rows, cursorRow: i + 1, cursorCol: 0 };
    }
  }

  // Locate the cursor row: the last row whose start <= cursorDisp and (cursorDisp <= end). Ties at a
  // soft-wrap seam (cursorDisp === row.end === nextRow.start) resolve to the LATER row, EXCEPT a row
  // ended by a hard '\n' (where end < nextRow.start) keeps the cursor on the line it typed.
  let cursorRow = 0;
  let cursorCol = 0;
  for (let i = 0; i < drows.length; i++) {
    const r = drows[i];
    if (r === undefined) {
      continue;
    }
    if (cursorDisp >= r.start && cursorDisp <= r.end) {
      cursorRow = i;
      cursorCol = cursorDisp - r.start;
      // If we're exactly at this row's end and the next row begins right here (soft seam, no '\n'
      // between), the cursor belongs at the head of the next row.
      const next = drows[i + 1];
      if (next !== undefined && cursorDisp === r.end && next.start === r.end) {
        cursorRow = i + 1;
        cursorCol = 0;
      }
      break;
    }
    // Cursor past this row but before the next row's start (it sat in a consumed '\n' / space seam):
    // keep walking; the loop's final iteration covers the buffer-end case below.
    if (i === drows.length - 1 && cursorDisp > r.end) {
      cursorRow = i;
      cursorCol = r.end - r.start;
    }
  }

  return { rows, cursorRow, cursorCol };
}

/** Find the buffer offset for a target (row, col) in the given layout, snapping to span boundaries. */
function bufferOffsetAt(
  s: BufferState,
  width: number,
  targetRow: number,
  targetCol: number,
): number {
  const w = width < 1 ? 1 : Math.floor(width);
  const map = buildDisplayMap(s.text);
  const drows = wrapDisplay(map.display, w);
  const row = drows[targetRow];
  if (row === undefined) {
    return s.cursor;
  }
  const rowLen = row.end - row.start;
  const col = targetCol > rowLen ? rowLen : targetCol;
  const disp = row.start + col;
  return map.displayToBuf(disp);
}

/**
 * Move the cursor up one VISUAL row, preserving the target column. Returns the new BufferState, or
 * `null` when already on the top visual row (the caller treats null `up` as "recall history").
 */
export function visualUp(s: BufferState, width: number): BufferState | null {
  const { cursorRow, cursorCol } = layout(s, width);
  if (cursorRow === 0) {
    return null;
  }
  return { text: s.text, cursor: bufferOffsetAt(s, width, cursorRow - 1, cursorCol) };
}

/**
 * Move the cursor down one VISUAL row, preserving the target column. Returns the new BufferState, or
 * `null` when already on the bottom visual row (the caller treats null `down` as "restore draft").
 */
export function visualDown(s: BufferState, width: number): BufferState | null {
  const lay = layout(s, width);
  if (lay.cursorRow >= lay.rows.length - 1) {
    return null;
  }
  return { text: s.text, cursor: bufferOffsetAt(s, width, lay.cursorRow + 1, lay.cursorCol) };
}
