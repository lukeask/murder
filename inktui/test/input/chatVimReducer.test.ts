/**
 * `chatVimReducer` tests — every normal-mode command → expected {@link VimEffect}.
 *
 * The reducer is pure and composes the chatBuffer ops (the WS-A module, `src/input/chatBuffer.ts`).
 * These tests run against the REAL chatBuffer (it has landed), so the motion offsets below are exactly
 * vim's: e.g. `e` lands ON the word's last char (cursor 2 in "foo bar"), not just past it.
 *
 * Coverage: mode entry (i/a/I/A/o/O), Esc-from-pending, motions (h/l/w/b/e/0/^/$/G/gg, arrows), j/k
 * logical fallback, x, D, d+motion, dd, c+motion, cc, y+motion, yy, p, P, and unknown keys.
 */

import { describe, expect, it } from 'vitest';
import type { BufferState } from '../../src/input/chatBuffer.js';
import { SPAN_CLOSE, SPAN_OPEN } from '../../src/input/chatInputStore.js';
import { reduceVimNormal, type VimEffect } from '../../src/input/chatVimReducer.js';
import { makeKey } from './key.js';

const buf = (text: string, cursor: number): BufferState => ({ text, cursor });
/** Wrap an id into its raw image-span text (mirrors makeSpan; kept local so tests are explicit). */
const span = (id: string): string => `${SPAN_OPEN}${id}${SPAN_CLOSE}`;
/** Reduce a single printable key (no modifier flags set). */
const press = (
  s: BufferState,
  input: string,
  pending: string | null = null,
  register = '',
): VimEffect => reduceVimNormal(s, input, makeKey(), pending, register);

describe('chatVimReducer — mode entry', () => {
  it('i enters insert at the cursor', () => {
    const e = press(buf('abc', 1), 'i');
    expect(e).toEqual({ kind: 'enterInsert', state: buf('abc', 1) });
  });

  it('a enters insert after the cursor', () => {
    const e = press(buf('abc', 1), 'a');
    expect(e).toEqual({ kind: 'enterInsert', state: buf('abc', 2) });
  });

  it('I enters insert at logical line start', () => {
    const e = press(buf('ab\ncd', 4), 'I');
    expect(e).toEqual({ kind: 'enterInsert', state: buf('ab\ncd', 3) });
  });

  it('A enters insert at logical line end', () => {
    const e = press(buf('ab\ncd', 3), 'A');
    expect(e).toEqual({ kind: 'enterInsert', state: buf('ab\ncd', 5) });
  });

  it('o opens a line below and enters insert at its start', () => {
    const e = press(buf('ab\ncd', 1), 'o');
    expect(e).toEqual({ kind: 'enterInsert', state: buf('ab\n\ncd', 3) });
  });

  it('O opens a line above and enters insert at its start', () => {
    const e = press(buf('ab\ncd', 4), 'O');
    expect(e).toEqual({ kind: 'enterInsert', state: buf('ab\n\ncd', 3) });
  });
});

describe('chatVimReducer — motions', () => {
  it('h moves left, l moves right', () => {
    expect(press(buf('abc', 1), 'h')).toEqual({ kind: 'buffer', state: buf('abc', 0) });
    expect(press(buf('abc', 1), 'l')).toEqual({ kind: 'buffer', state: buf('abc', 2) });
  });

  it('arrows act as h/l motions', () => {
    expect(reduceVimNormal(buf('abc', 1), '', makeKey({ leftArrow: true }), null, '')).toEqual({
      kind: 'buffer',
      state: buf('abc', 0),
    });
    expect(reduceVimNormal(buf('abc', 1), '', makeKey({ rightArrow: true }), null, '')).toEqual({
      kind: 'buffer',
      state: buf('abc', 2),
    });
  });

  it('w / b / e word motions', () => {
    expect(press(buf('foo bar', 0), 'w')).toEqual({ kind: 'buffer', state: buf('foo bar', 4) });
    expect(press(buf('foo bar', 4), 'b')).toEqual({ kind: 'buffer', state: buf('foo bar', 0) });
    // `e` lands ON the word's last char — index 2 ('o') in "foo bar".
    expect(press(buf('foo bar', 0), 'e')).toEqual({ kind: 'buffer', state: buf('foo bar', 2) });
  });

  it('0 and ^ go to line start, $ to line end', () => {
    expect(press(buf('ab\ncd', 4), '0')).toEqual({ kind: 'buffer', state: buf('ab\ncd', 3) });
    expect(press(buf('ab\ncd', 4), '^')).toEqual({ kind: 'buffer', state: buf('ab\ncd', 3) });
    expect(press(buf('ab\ncd', 3), '$')).toEqual({ kind: 'buffer', state: buf('ab\ncd', 5) });
  });

  it('G goes to buffer end; gg goes to buffer start', () => {
    expect(press(buf('abc', 0), 'G')).toEqual({ kind: 'buffer', state: buf('abc', 3) });
    // gg is two keys: first g → pending, second g → buffer start.
    expect(press(buf('abc', 2), 'g')).toEqual({ kind: 'pending', pending: 'g' });
    expect(press(buf('abc', 2), 'g', 'g')).toEqual({ kind: 'buffer', state: buf('abc', 0) });
  });

  it('an unknown g-suffix cancels the pending g', () => {
    expect(press(buf('abc', 2), 'z', 'g')).toEqual({ kind: 'pending', pending: null });
  });

  it('j / k fall back to logical line down/up at the same column', () => {
    // 'ab\ncde', cursor at col 1 of line 0 (index 1) → j → col 1 of line 1 (index 4).
    expect(press(buf('ab\ncde', 1), 'j')).toEqual({ kind: 'buffer', state: buf('ab\ncde', 4) });
    // cursor at index 4 (col 1 of line 1) → k → col 1 of line 0 (index 1).
    expect(press(buf('ab\ncde', 4), 'k')).toEqual({ kind: 'buffer', state: buf('ab\ncde', 1) });
  });

  it('j clamps to a shorter next line', () => {
    // line0 'abcd' (col 3), line1 'x' → j clamps col 3 to end of 'x' (index 6).
    expect(press(buf('abcd\nx', 3), 'j')).toEqual({ kind: 'buffer', state: buf('abcd\nx', 6) });
  });

  it('j on the last line is a no-op', () => {
    expect(press(buf('ab\ncd', 4), 'j')).toEqual({ kind: 'buffer', state: buf('ab\ncd', 4) });
  });

  it('k on the first line is a no-op', () => {
    expect(press(buf('ab\ncd', 1), 'k')).toEqual({ kind: 'buffer', state: buf('ab\ncd', 1) });
  });
});

describe('chatVimReducer — single-key edits', () => {
  it('x deletes the char at the cursor', () => {
    expect(press(buf('abc', 1), 'x')).toEqual({ kind: 'buffer', state: buf('ac', 1) });
  });

  it('D deletes to line end and sets the register', () => {
    expect(press(buf('ab\ncd', 3), 'D')).toEqual({
      kind: 'setRegister',
      register: 'cd',
      state: buf('ab\n', 3),
    });
  });
});

describe('chatVimReducer — operators with motion', () => {
  it('dw deletes a word forward and sets the register', () => {
    expect(press(buf('foo bar', 0), 'w', 'd')).toEqual({
      kind: 'setRegister',
      register: 'foo ',
      state: buf('bar', 0),
    });
  });

  it('db deletes a word backward', () => {
    expect(press(buf('foo bar', 4), 'b', 'd')).toEqual({
      kind: 'setRegister',
      register: 'foo ',
      state: buf('bar', 0),
    });
  });

  it('d$ deletes to line end', () => {
    expect(press(buf('ab\ncd', 3), '$', 'd')).toEqual({
      kind: 'setRegister',
      register: 'cd',
      state: buf('ab\n', 3),
    });
  });

  it('d0 deletes to line start', () => {
    expect(press(buf('abcd', 2), '0', 'd')).toEqual({
      kind: 'setRegister',
      register: 'ab',
      state: buf('cd', 0),
    });
  });

  it('an invalid motion after d cancels the operator', () => {
    expect(press(buf('abc', 0), 'z', 'd')).toEqual({ kind: 'pending', pending: null });
  });

  it('Esc cancels a pending operator', () => {
    expect(reduceVimNormal(buf('abc', 0), '', makeKey({ escape: true }), 'd', '')).toEqual({
      kind: 'pending',
      pending: null,
    });
  });

  it('cw deletes the word and enters insert (no register write per locked union)', () => {
    expect(press(buf('foo bar', 0), 'w', 'c')).toEqual({
      kind: 'enterInsert',
      state: buf('bar', 0),
    });
  });

  it('yw yanks a word without changing the buffer, parking the cursor at range start', () => {
    expect(press(buf('foo bar', 0), 'w', 'y')).toEqual({
      kind: 'setRegister',
      register: 'foo ',
      state: buf('foo bar', 0),
    });
  });
});

describe('chatVimReducer — line-wise operators', () => {
  it('dd deletes the whole line (incl. trailing newline) and yanks line+\\n', () => {
    expect(press(buf('ab\ncd\nef', 1), 'd', 'd')).toEqual({
      kind: 'setRegister',
      register: 'ab\n',
      state: buf('cd\nef', 0),
    });
  });

  it('dd on the last line consumes the preceding newline', () => {
    expect(press(buf('ab\ncd', 4), 'd', 'd')).toEqual({
      kind: 'setRegister',
      register: 'cd\n',
      state: buf('ab', 2),
    });
  });

  it('dd on the only line empties the buffer', () => {
    expect(press(buf('ab', 1), 'd', 'd')).toEqual({
      kind: 'setRegister',
      register: 'ab\n',
      state: buf('', 0),
    });
  });

  it('cc clears the line content, keeps the line, enters insert at its start', () => {
    expect(press(buf('ab\ncd\nef', 4), 'c', 'c')).toEqual({
      kind: 'enterInsert',
      state: buf('ab\n\nef', 3),
    });
  });

  it('yy yanks the line + newline without changing the buffer', () => {
    expect(press(buf('ab\ncd', 1), 'y', 'y')).toEqual({
      kind: 'setRegister',
      register: 'ab\n',
      state: buf('ab\ncd', 0),
    });
  });
});

describe('chatVimReducer — paste', () => {
  it('p pastes a char-wise register after the cursor char', () => {
    // register 'XY' (char-wise, no trailing \n) at cursor 0 of 'ab' → insert after index 0.
    expect(press(buf('ab', 0), 'p', null, 'XY')).toEqual({
      kind: 'paste',
      state: buf('aXYb', 2),
    });
  });

  it('P pastes a char-wise register before the cursor', () => {
    expect(press(buf('ab', 1), 'P', null, 'XY')).toEqual({
      kind: 'paste',
      state: buf('aXYb', 3),
    });
  });

  it('p pastes a line-wise register (trailing \\n) as a new line below', () => {
    // register 'cd\n' (line-wise) with buffer 'ab', cursor 1 → new line below: 'ab\ncd'.
    expect(press(buf('ab', 1), 'p', null, 'cd\n')).toEqual({
      kind: 'paste',
      state: buf('ab\ncd', 3),
    });
  });

  it('p with an empty register is a no-op paste', () => {
    expect(press(buf('ab', 1), 'p', null, '')).toEqual({ kind: 'paste', state: buf('ab', 1) });
  });
});

describe('chatVimReducer — image-span boundary invariant (j/k/p never land inside a span)', () => {
  // `span('img')` is U+E000 'img' U+E001 — a 5-char atomic unit; its interior offsets are invalid
  // cursor positions. These ops compute target offsets by raw arithmetic, so they must span-snap.

  it('j (logical line down) snaps onto a span instead of inside it', () => {
    // Line 0 = 'x' (offset 0), line 1 = a lone span (offsets 2..7). From col 1 on line 0, the
    // column-preserving target is offset 3 — strictly inside the span — and must snap to its start (2).
    const text = `x\n${span('img')}`;
    const e = press(buf(text, 1), 'j');
    expect(e).toEqual({ kind: 'buffer', state: buf(text, 2) });
  });

  it('k (logical line up) snaps onto a span instead of inside it', () => {
    // Line 0 = a lone span (offsets 0..4), line 1 = 'y'. From col 1 on line 1, the target is offset 1
    // — inside the span — and must snap to its start (0).
    const text = `${span('im')}\ny`;
    const cursor = text.length; // on 'y'
    const e = press(buf(text, cursor), 'k');
    expect(e).toEqual({ kind: 'buffer', state: buf(text, 0) });
  });

  it('p never splices the register into the middle of a span', () => {
    // Cursor at the span's leading edge (offset 2). Naive paste-after inserts at offset 3 — mid-span —
    // corrupting the markers/id (it would yield `abZimgcd` with the open marker eaten). The insertion
    // point must snap to the span boundary so the span stays a verbatim, atomic unit.
    const text = `ab${span('img')}cd`;
    const leadingEdge = 2;
    const e = press(buf(text, leadingEdge), 'p', null, 'Z');
    if (e.kind !== 'paste') {
      throw new Error('expected paste effect');
    }
    // The span text survives intact (snapping pulled the insert to the span's leading edge).
    expect(e.state.text).toBe(`abZ${span('img')}cd`);
    expect(e.state.text).toContain(span('img'));
    // Cursor lands on a valid boundary (never strictly inside the span interior).
    const spanStart = e.state.text.indexOf(SPAN_OPEN);
    const spanEnd = e.state.text.indexOf(SPAN_CLOSE) + 1;
    const insideSpan = e.state.cursor > spanStart && e.state.cursor < spanEnd;
    expect(insideSpan).toBe(false);
  });
});

describe('chatVimReducer — unknown keys', () => {
  it('an unmapped key yields none', () => {
    expect(press(buf('abc', 0), 'Z')).toEqual({ kind: 'none' });
  });
});
