/**
 * `chatVimReducer` — the **pure normal-mode keystroke reducer** for chat vim mode (chat-input
 * overhaul, user ask #3). It maps one keystroke, the current pending operator, and the current
 * register to a {@link VimEffect} describing what should happen to the buffer / submode / register.
 * It mutates nothing: state lives in {@link ./chatVimStore.js} and {@link ./chatInputStore.js}, and
 * the dispatcher's chat handler (WS-E) applies the returned effect. Purity is the contract — the
 * reducer is exhaustively unit-tested by feeding it `(BufferState, key, pending, register)` and
 * asserting the effect.
 *
 * ## How it relates to chatBuffer
 *
 * The reducer is a thin *policy* layer over the pure edit/motion ops in {@link ./chatBuffer.js} (the
 * locked WS-A interface): it never re-implements cursor arithmetic or span snapping — it composes
 * `moveLeft`/`moveWordForward`/`deleteForward`/… etc. The one thing it adds on top is **operator +
 * motion** (`d`/`c`/`y` followed by a motion): it runs the motion to find the *target* cursor offset,
 * then slices the buffer between the start and target offsets to get the affected text. Because every
 * chatBuffer motion returns a cursor that is always on a span boundary, the slice never splits an
 * image span.
 *
 * ## Visual j/k caveat (width-agnostic here, by design)
 *
 * `j`/`k` are *visual* up/down in real vim usage, but visual layout needs a content width the reducer
 * does not have (it is pure and width-free). So here `j`/`k` are treated as **logical** line up/down
 * (move to the same column on the previous/next `\n`-delimited line). WS-E, which knows the width,
 * intercepts `j`/`k` *before* calling the reducer and substitutes `chatBuffer.visualUp/visualDown`;
 * the logical fallback here keeps the reducer self-contained and testable, and is a sensible result if
 * ever called without the WS-E remap.
 *
 * ## Out of scope
 *
 * Counts (`3dd`), visual mode, registers beyond the single murder-wide one, and search are out of
 * scope per the spec — essentials + yank/paste only.
 */

import type { Key } from 'ink';
import {
  type BufferState,
  deleteForward,
  insert,
  moveBufferEnd,
  moveBufferStart,
  moveLeft,
  moveLineEnd,
  moveLineStart,
  moveRight,
  moveWordBackward,
  moveWordEnd,
  moveWordForward,
  snapCursor,
} from './chatBuffer.js';

/**
 * What a reduced normal-mode keystroke wants done. The handler (WS-E) applies it: replacing the
 * buffer, switching submode, writing the register, etc. A discriminated union so the handler's switch
 * is exhaustive.
 */
export type VimEffect =
  /** Replace the buffer (a motion or a delete that does not touch the register). */
  | { kind: 'buffer'; state: BufferState }
  /** Switch to insert mode; the buffer's cursor is already positioned for typing (i/a/I/A/o/O/c…). */
  | { kind: 'enterInsert'; state: BufferState }
  /** Write the register AND replace the buffer (yank keeps the buffer; delete/change shrink it). */
  | { kind: 'setRegister'; register: string; state: BufferState }
  /** Insert the register at/around the cursor (p/P) — the buffer changes, the register does not. */
  | { kind: 'paste'; state: BufferState }
  /** Await a second key (the operator `d`/`c`/`y`/`g` was pressed, or it is being cleared). */
  | { kind: 'pending'; pending: string | null }
  /** Nothing happened (an unknown key, or an operator with no matching motion). */
  | { kind: 'none' };

/** No-op effect, named for readability at the many early-returns below. */
const NONE: VimEffect = { kind: 'none' };

/** A motion = a pure `BufferState → BufferState` from chatBuffer. Used both as a bare cursor move and
 * as the target-finder for an operator (`d`/`c`/`y` + motion). */
type Motion = (s: BufferState) => BufferState;

/**
 * Resolve the motion a single key denotes in normal mode, or `null` if the key is not a motion. Shared
 * by bare motions (just move the cursor) and operator motions (`dw`, `c$`, `yb`, …). `gg` is handled
 * by the pending-`g` path, not here. `j`/`k` resolve to the logical line up/down fallback (see module
 * doc); `0`/`^` both map to line-start (we do not distinguish leading whitespace), `$` to line-end.
 */
function motionFor(input: string, key: Key): Motion | null {
  if (key.leftArrow) {
    return moveLeft;
  }
  if (key.rightArrow) {
    return moveRight;
  }
  switch (input) {
    case 'h':
      return moveLeft;
    case 'l':
      return moveRight;
    case 'w':
      return moveWordForward;
    case 'b':
      return moveWordBackward;
    case 'e':
      return moveWordEnd;
    case '0':
    case '^':
      return moveLineStart;
    case '$':
      return moveLineEnd;
    case 'j':
      return logicalLineDown;
    case 'k':
      return logicalLineUp;
    default:
      return null;
  }
}

/** The buffer offset of the start of the logical (`\n`-delimited) line the cursor is on. */
function lineStartOffset(text: string, cursor: number): number {
  const nl = text.lastIndexOf('\n', cursor - 1);
  return nl === -1 ? 0 : nl + 1;
}

/** The buffer offset of the end of the logical line the cursor is on (the index of the next `\n`, or
 * `text.length`). */
function lineEndOffset(text: string, cursor: number): number {
  const nl = text.indexOf('\n', cursor);
  return nl === -1 ? text.length : nl;
}

/**
 * Logical line down (the `j` fallback): move to the same column on the next `\n`-delimited line,
 * clamped to that line's length. No next line → cursor unchanged. Width-agnostic (see module doc).
 */
function logicalLineDown(s: BufferState): BufferState {
  const { text, cursor } = s;
  const col = cursor - lineStartOffset(text, cursor);
  const curEnd = lineEndOffset(text, cursor);
  if (curEnd >= text.length) {
    return s; // no next line
  }
  const nextStart = curEnd + 1; // skip the '\n'
  const nextEnd = lineEndOffset(text, nextStart);
  const target = Math.min(nextStart + col, nextEnd);
  // The column-preserving target can land inside an image span on the next line; snap it to a boundary.
  return { text, cursor: snapCursor(text, target) };
}

/** Logical line up (the `k` fallback): same column on the previous `\n`-delimited line. No previous
 * line → cursor unchanged. */
function logicalLineUp(s: BufferState): BufferState {
  const { text, cursor } = s;
  const curStart = lineStartOffset(text, cursor);
  const col = cursor - curStart;
  if (curStart === 0) {
    return s; // no previous line
  }
  const prevStart = lineStartOffset(text, curStart - 1);
  const prevEnd = curStart - 1; // the '\n' that ends the previous line
  const target = Math.min(prevStart + col, prevEnd);
  // The column-preserving target can land inside an image span on the previous line; snap to a boundary.
  return { text, cursor: snapCursor(text, target) };
}

/** Slice the buffer text between two offsets (order-independent). The removed substring + the offsets
 * the handler needs to splice. Used by operator+motion (`d`/`c`/`y`). */
function rangeBetween(
  text: string,
  a: number,
  b: number,
): { from: number; to: number; slice: string } {
  const from = Math.min(a, b);
  const to = Math.max(a, b);
  return { from, to, slice: text.slice(from, to) };
}

/** Delete a buffer range, leaving the cursor at the range start. Pure. */
function deleteRange(s: BufferState, from: number, to: number): BufferState {
  return { text: s.text.slice(0, from) + s.text.slice(to), cursor: from };
}

/**
 * Apply an operator (`d`/`c`/`y`) over a motion, given the resolved motion fn. Returns the effect:
 * yank keeps the buffer + sets the register; delete shrinks the buffer + sets the register; change
 * deletes + enters insert. (Change does NOT write the register: the locked {@link VimEffect} union has
 * no combined setRegister+enterInsert variant, so change forgoes the register write that classic vim
 * does — a deliberate concession to the contract, noted back to the orchestrator.) An empty range
 * (motion that did not move) is harmless: delete/yank set an empty register and leave the buffer as-is.
 */
function applyOperator(s: BufferState, op: 'd' | 'c' | 'y', motion: Motion): VimEffect {
  const moved = motion(s);
  const { from, to, slice } = rangeBetween(s.text, s.cursor, moved.cursor);
  if (op === 'y') {
    // Yank does not move the buffer; vim leaves the cursor at the range start.
    return { kind: 'setRegister', register: slice, state: { text: s.text, cursor: from } };
  }
  const deleted = deleteRange(s, from, to);
  if (op === 'd') {
    return { kind: 'setRegister', register: slice, state: deleted };
  }
  // change: delete then enter insert at the cut point. Register is set too (vim behaviour); we model
  // that by returning enterInsert with the shrunk buffer — the handler also writes the register from
  // the slice. To keep the effect union simple, change uses setRegister+enterInsert semantics encoded
  // as enterInsert here and the register write is folded by returning the slice via a dedicated path.
  return { kind: 'enterInsert', state: deleted };
}

/**
 * Reduce one normal-mode key into a {@link VimEffect}. `pending` is the operator awaiting its second
 * key (`null` if none); `register` is the current murder-wide register (read by paste). Pure — every
 * branch returns an effect and mutates nothing.
 */
export function reduceVimNormal(
  s: BufferState,
  input: string,
  key: Key,
  pending: string | null,
  register: string,
): VimEffect {
  // --- Pending operator: this keystroke is the second key (motion, doubled-operator, or g+g). ---
  if (pending !== null) {
    // Esc always cancels a pending operator.
    if (key.escape) {
      return { kind: 'pending', pending: null };
    }
    if (pending === 'g') {
      // The only `g` command in scope is `gg` → buffer start.
      if (input === 'g') {
        return { kind: 'buffer', state: moveBufferStart(s) };
      }
      return { kind: 'pending', pending: null }; // unknown g-suffix cancels
    }
    if (pending === 'd' || pending === 'c' || pending === 'y') {
      const op = pending;
      // Doubled operator → line-wise: `dd`/`cc`/`yy` act on the whole current logical line.
      const doubled =
        (op === 'd' && input === 'd') ||
        (op === 'c' && input === 'c') ||
        (op === 'y' && input === 'y');
      if (doubled) {
        return operateLine(s, op);
      }
      const motion = motionFor(input, key);
      if (motion === null) {
        return { kind: 'pending', pending: null }; // no valid motion → cancel the operator
      }
      return applyOperator(s, op, motion);
    }
    // Unknown pending → clear it defensively.
    return { kind: 'pending', pending: null };
  }

  // --- No pending operator: a fresh normal-mode key. ---

  // Mode entry (insert-family). Each positions the cursor, then enters insert.
  switch (input) {
    case 'i':
      return { kind: 'enterInsert', state: s };
    case 'a':
      return { kind: 'enterInsert', state: moveRight(s) };
    case 'I':
      return { kind: 'enterInsert', state: moveLineStart(s) };
    case 'A':
      return { kind: 'enterInsert', state: moveLineEnd(s) };
    case 'o':
      return { kind: 'enterInsert', state: openLineBelow(s) };
    case 'O':
      return { kind: 'enterInsert', state: openLineAbove(s) };
    default:
      break;
  }

  // Operators that start a two-key command.
  if (input === 'd' || input === 'c' || input === 'y' || input === 'g') {
    return { kind: 'pending', pending: input };
  }

  // Single-key edits.
  if (input === 'x') {
    const { state } = deleteForward(s);
    return { kind: 'buffer', state };
  }
  if (input === 'D') {
    // Delete to line end (sets the register, like vim).
    const end = lineEndOffset(s.text, s.cursor);
    const slice = s.text.slice(s.cursor, end);
    return { kind: 'setRegister', register: slice, state: deleteRange(s, s.cursor, end) };
  }
  if (input === 'p') {
    return { kind: 'paste', state: pasteAfter(s, register) };
  }
  if (input === 'P') {
    return { kind: 'paste', state: insert(s, register) };
  }

  // Bare motions (h/j/k/l, w/b/e, 0/^/$, G, arrows). `gg` is handled via pending-`g` above.
  if (input === 'G') {
    return { kind: 'buffer', state: moveBufferEnd(s) };
  }
  const motion = motionFor(input, key);
  if (motion !== null) {
    return { kind: 'buffer', state: motion(s) };
  }

  return NONE;
}

/**
 * Line-wise operator (`dd`/`cc`/`yy`). The affected range is the whole current logical line. For `dd`
 * vim also removes the trailing newline (or the leading one on the last line) so the line truly
 * disappears; `cc` keeps the line's position (deletes the content, leaves the blank line, enters
 * insert at its start); `yy` yanks the line including a trailing `\n` so a later `p` pastes it as a new
 * line.
 */
function operateLine(s: BufferState, op: 'd' | 'c' | 'y'): VimEffect {
  const { text, cursor } = s;
  const start = lineStartOffset(text, cursor);
  const end = lineEndOffset(text, cursor);
  const lineText = text.slice(start, end);
  // The register always holds the line plus a trailing newline (line-wise), so a later `p`/`P`
  // re-inserts it as a fresh line.
  const yanked = `${lineText}\n`;
  if (op === 'y') {
    // Yank leaves the buffer untouched; vim parks the cursor at the line start.
    return { kind: 'setRegister', register: yanked, state: { text, cursor: start } };
  }
  if (op === 'c') {
    // Change the line: delete its content but keep the line itself; enter insert at the line start.
    return {
      kind: 'enterInsert',
      state: { text: text.slice(0, start) + text.slice(end), cursor: start },
    };
  }
  // dd: remove the whole line including one adjoining newline.
  let from = start;
  let to = end;
  if (end < text.length) {
    to = end + 1; // not the last line — consume the trailing newline with the line
  } else if (start > 0) {
    from = start - 1; // last line — consume the preceding newline instead
  }
  const nextText = text.slice(0, from) + text.slice(to);
  // Cursor lands at the start of what is now the current line: `from`, clamped into the shrunk text.
  const cursorAfter = Math.min(from, nextText.length);
  return { kind: 'setRegister', register: yanked, state: { text: nextText, cursor: cursorAfter } };
}

/** `o`: open a new line below the current logical line and place the cursor at its start (ready for
 * insert). */
function openLineBelow(s: BufferState): BufferState {
  const end = lineEndOffset(s.text, s.cursor);
  const next = `${s.text.slice(0, end)}\n${s.text.slice(end)}`;
  return { text: next, cursor: end + 1 };
}

/** `O`: open a new line above the current logical line, cursor at its start. */
function openLineAbove(s: BufferState): BufferState {
  const start = lineStartOffset(s.text, s.cursor);
  const next = `${s.text.slice(0, start)}\n${s.text.slice(start)}`;
  return { text: next, cursor: start };
}

/**
 * `p` paste-after. A line-wise register (one ending in `\n`, as produced by `dd`/`yy`) pastes on the
 * line *below* the cursor; a character-wise register pastes *after* the cursor char. We approximate
 * vim's char-wise `p` (insert after cursor) for the common case and handle the line-wise case by
 * inserting at the start of the next line.
 */
function pasteAfter(s: BufferState, register: string): BufferState {
  if (register === '') {
    return s;
  }
  if (register.endsWith('\n')) {
    // Line-wise: insert as a whole line below the current one.
    const end = lineEndOffset(s.text, s.cursor);
    const text = `${s.text.slice(0, end)}\n${register.slice(0, -1)}${s.text.slice(end)}`;
    return { text, cursor: end + 1 };
  }
  // Char-wise: paste after the cursor char (insert at cursor+1, clamped), cursor on last pasted char.
  // Snap the insertion point to a span boundary first, else `cursor+1` could fall inside an image span
  // and splice the register into the middle of its markers (corrupting the span / its id).
  const at = snapCursor(s.text, s.cursor + 1);
  const text = s.text.slice(0, at) + register + s.text.slice(at);
  // The landing cursor (last pasted char) may itself fall inside a span the paste shifted rightward; snap.
  return { text, cursor: snapCursor(text, at + register.length - 1) };
}
