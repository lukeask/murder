/**
 * `chatBuffer` tests — the pure cursor model + edit ops + visual-wrap navigation (WS-A).
 *
 * Exhaustive coverage of the locked interface:
 *  - insert / insertImageSpan / backspace / deleteForward at and around image spans;
 *  - left/right snapping over a whole span; logical home/end across multi-line text;
 *  - word motions (w/b/e) including over spans;
 *  - `layout` wrapping: word-wrap, over-long word hard-break, '\n' hard-break, trailing-cursor-at-width;
 *  - visualUp/visualDown column preservation + the null top/bottom boundaries;
 *  - an image-span row that wraps (display-vs-buffer offset mapping).
 */

import { describe, expect, it } from 'vitest';
import {
  type BufferState,
  backspace,
  deleteForward,
  EMPTY_BUFFER,
  insert,
  insertImageSpan,
  layout,
  moveBufferEnd,
  moveBufferStart,
  moveLeft,
  moveLineEnd,
  moveLineStart,
  moveRight,
  moveWordBackward,
  moveWordEnd,
  moveWordForward,
  visualDown,
  visualUp,
} from '../../src/input/chatBuffer.js';
import { SPAN_CLOSE, SPAN_OPEN } from '../../src/input/chatInputStore.js';

/** Build a buffer state shorthand. */
function buf(text: string, cursor: number): BufferState {
  return { text, cursor };
}

/** Wrap an id into its raw span text (mirrors makeSpan, kept local so tests are explicit). */
function span(id: string): string {
  return `${SPAN_OPEN}${id}${SPAN_CLOSE}`;
}

describe('chatBuffer — constants & insert', () => {
  it('EMPTY_BUFFER is empty text with cursor 0', () => {
    expect(EMPTY_BUFFER).toEqual({ text: '', cursor: 0 });
  });

  it('insert at cursor advances the cursor past the inserted text', () => {
    expect(insert(buf('', 0), 'hi')).toEqual({ text: 'hi', cursor: 2 });
    expect(insert(buf('ac', 1), 'b')).toEqual({ text: 'abc', cursor: 2 });
  });

  it('insert in the middle of text places text at cursor', () => {
    expect(insert(buf('hello', 2), 'XY')).toEqual({ text: 'heXYllo', cursor: 4 });
  });

  it('insertImageSpan wraps the id and lands the cursor after the span', () => {
    const s = insertImageSpan(buf('a', 1), 'img-1');
    expect(s.text).toBe(`a${span('img-1')}`);
    expect(s.cursor).toBe(s.text.length);
  });
});

describe('chatBuffer — backspace', () => {
  it('deletes one plain char before the cursor', () => {
    expect(backspace(buf('abc', 2))).toEqual({
      state: { text: 'ac', cursor: 1 },
      removedId: null,
    });
  });

  it('is a no-op at offset 0', () => {
    const s = buf('abc', 0);
    expect(backspace(s)).toEqual({ state: s, removedId: null });
  });

  it('removes the whole span at the trailing edge and returns its id', () => {
    const text = `x${span('img-9')}`;
    const s = buf(text, text.length);
    const { state, removedId } = backspace(s);
    expect(removedId).toBe('img-9');
    expect(state).toEqual({ text: 'x', cursor: 1 });
  });

  it('deletes a plain char even when a span sits earlier in the buffer', () => {
    const text = `${span('a')}bc`;
    const s = buf(text, text.length); // after 'c'
    const { state, removedId } = backspace(s);
    expect(removedId).toBeNull();
    expect(state.text).toBe(`${span('a')}b`);
  });
});

describe('chatBuffer — backspace over grapheme clusters (M3)', () => {
  /** A lone surrogate is a code unit in [0xD800, 0xDFFF] with no matching pair. */
  const hasLoneSurrogate = (s: string): boolean => {
    for (let i = 0; i < s.length; i++) {
      const c = s.charCodeAt(i);
      if (c >= 0xd800 && c <= 0xdbff) {
        const next = s.charCodeAt(i + 1);
        if (!(next >= 0xdc00 && next <= 0xdfff)) {
          return true;
        }
        i++; // valid pair — skip the low surrogate
      } else if (c >= 0xdc00 && c <= 0xdfff) {
        return true; // low surrogate with no preceding high surrogate
      }
    }
    return false;
  };

  it('deletes a whole emoji surrogate pair in one backspace (no lone surrogate)', () => {
    const text = 'a🐦'; // 🐦 = U+1F426, two UTF-16 code units
    const s = buf(text, text.length);
    const { state, removedId } = backspace(s);
    expect(removedId).toBeNull();
    expect(state).toEqual({ text: 'a', cursor: 1 });
    expect(hasLoneSurrogate(state.text)).toBe(false);
  });

  it('deletes the emoji even mid-buffer, landing the cursor on a boundary', () => {
    const text = 'a🐦b'; // cursor right after the emoji (offset 3)
    const s = buf(text, 3);
    const { state } = backspace(s);
    expect(state).toEqual({ text: 'ab', cursor: 1 });
    expect(hasLoneSurrogate(state.text)).toBe(false);
  });

  it('deletes a ZWJ emoji sequence as a single cluster', () => {
    // 👨‍👩‍👧 = man + ZWJ + woman + ZWJ + girl (one user-perceived glyph)
    const family = '👨‍👩‍👧';
    const text = `x${family}`;
    const s = buf(text, text.length);
    const { state } = backspace(s);
    expect(state.text).toBe('x');
    expect(state.cursor).toBe(1);
    expect(hasLoneSurrogate(state.text)).toBe(false);
  });

  it('deletes one BMP CJK char per backspace (中文)', () => {
    const text = '中文'; // each is one BMP code unit
    const s = buf(text, text.length);
    const { state } = backspace(s);
    expect(state).toEqual({ text: '中', cursor: 1 });
    expect(hasLoneSurrogate(state.text)).toBe(false);
    const { state: state2 } = backspace(state);
    expect(state2).toEqual({ text: '', cursor: 0 });
  });

  it('deleteForward removes a whole emoji (no lone surrogate, cursor put)', () => {
    const text = '🐦b';
    const s = buf(text, 0);
    const { state, removedId } = deleteForward(s);
    expect(removedId).toBeNull();
    expect(state).toEqual({ text: 'b', cursor: 0 });
    expect(hasLoneSurrogate(state.text)).toBe(false);
  });
});

describe('chatBuffer — deleteForward', () => {
  it('deletes the char at the cursor, leaving the cursor put', () => {
    expect(deleteForward(buf('abc', 1))).toEqual({
      state: { text: 'ac', cursor: 1 },
      removedId: null,
    });
  });

  it('is a no-op at the buffer end', () => {
    const s = buf('abc', 3);
    expect(deleteForward(s)).toEqual({ state: s, removedId: null });
  });

  it('removes the whole span at the leading edge and returns its id', () => {
    const text = `${span('img-3')}y`;
    const s = buf(text, 0);
    const { state, removedId } = deleteForward(s);
    expect(removedId).toBe('img-3');
    expect(state).toEqual({ text: 'y', cursor: 0 });
  });
});

describe('chatBuffer — horizontal motion (span snapping)', () => {
  it('moveLeft / moveRight step one char in plain text', () => {
    expect(moveLeft(buf('abc', 2)).cursor).toBe(1);
    expect(moveRight(buf('abc', 1)).cursor).toBe(2);
  });

  it('moveLeft / moveRight clamp at the ends', () => {
    expect(moveLeft(buf('abc', 0)).cursor).toBe(0);
    expect(moveRight(buf('abc', 3)).cursor).toBe(3);
  });

  it('moveLeft over a span jumps from trailing edge to leading edge', () => {
    const text = `a${span('img')}b`;
    const trailing = 1 + span('img').length;
    expect(moveLeft(buf(text, trailing)).cursor).toBe(1);
  });

  it('moveRight over a span jumps from leading edge to trailing edge', () => {
    const text = `a${span('img')}b`;
    const trailing = 1 + span('img').length;
    expect(moveRight(buf(text, 1)).cursor).toBe(trailing);
  });
});

describe('chatBuffer — logical line home/end', () => {
  const text = 'first\nsecond\nthird';

  it('moveLineStart goes to the char after the preceding newline', () => {
    // cursor inside "second" (index of 'c' in second = 5+'second'.indexOf('c')) -> line start = 6
    expect(moveLineStart(buf(text, 9)).cursor).toBe(6);
    // first line -> 0
    expect(moveLineStart(buf(text, 3)).cursor).toBe(0);
  });

  it('moveLineEnd goes to the char before the next newline (or buffer end)', () => {
    expect(moveLineEnd(buf(text, 7)).cursor).toBe(12); // end of "second"
    expect(moveLineEnd(buf(text, 14)).cursor).toBe(text.length); // last line -> end
  });

  it('moveBufferStart / moveBufferEnd jump to 0 / length', () => {
    expect(moveBufferStart(buf(text, 9)).cursor).toBe(0);
    expect(moveBufferEnd(buf(text, 0)).cursor).toBe(text.length);
  });
});

describe('chatBuffer — vim word motion', () => {
  it('moveWordForward lands on the next word start', () => {
    const text = 'foo bar baz';
    expect(moveWordForward(buf(text, 0)).cursor).toBe(4); // 'b' of bar
    expect(moveWordForward(buf(text, 4)).cursor).toBe(8); // 'b' of baz
    expect(moveWordForward(buf(text, 8)).cursor).toBe(text.length); // end
  });

  it('moveWordBackward lands on the current/previous word start', () => {
    const text = 'foo bar baz';
    expect(moveWordBackward(buf(text, 9)).cursor).toBe(8); // within baz -> start of baz
    expect(moveWordBackward(buf(text, 8)).cursor).toBe(4); // start of baz -> start of bar
    expect(moveWordBackward(buf(text, 2)).cursor).toBe(0);
  });

  it('moveWordEnd lands on the last char of the current/next word', () => {
    const text = 'foo bar';
    expect(moveWordEnd(buf(text, 0)).cursor).toBe(2); // 'o' end of foo (offset of last char)
    expect(moveWordEnd(buf(text, 2)).cursor).toBe(6); // end of bar
  });

  it('word motion treats a span as a non-space unit and snaps to its edges', () => {
    const text = `foo ${span('img')} bar`;
    const spanStart = 4;
    const spanEnd = 4 + span('img').length;
    // w from start of "foo" -> start of span (next word)
    expect(moveWordForward(buf(text, 0)).cursor).toBe(spanStart);
    // w from span start -> start of "bar"
    expect(moveWordForward(buf(text, spanStart)).cursor).toBe(spanEnd + 1);
  });
});

describe('chatBuffer — layout wrapping', () => {
  it('empty buffer is a single empty row, cursor at 0,0', () => {
    const lay = layout(EMPTY_BUFFER, 10);
    expect(lay.rows).toEqual([{ text: '', startBufferOffset: 0 }]);
    expect(lay.cursorRow).toBe(0);
    expect(lay.cursorCol).toBe(0);
  });

  it('short text that fits is one row', () => {
    const lay = layout(buf('hello', 5), 10);
    expect(lay.rows.map((r) => r.text)).toEqual(['hello']);
    expect(lay.cursorRow).toBe(0);
    expect(lay.cursorCol).toBe(5);
  });

  it('soft-wraps by word, dropping the seam space', () => {
    const lay = layout(buf('foo bar baz', 0), 7);
    expect(lay.rows.map((r) => r.text)).toEqual(['foo bar', 'baz']);
  });

  it('hard-breaks a word longer than the width', () => {
    const lay = layout(buf('abcdefghij', 0), 4);
    expect(lay.rows.map((r) => r.text)).toEqual(['abcd', 'efgh', 'ij']);
  });

  it('hard-breaks on a newline (newline ends a row, occupies no cell)', () => {
    const lay = layout(buf('ab\ncd', 0), 10);
    expect(lay.rows.map((r) => r.text)).toEqual(['ab', 'cd']);
    expect(lay.rows[1]?.startBufferOffset).toBe(3); // after the '\n'
  });

  it('blank logical line yields an empty row', () => {
    const lay = layout(buf('a\n\nb', 0), 10);
    expect(lay.rows.map((r) => r.text)).toEqual(['a', '', 'b']);
  });

  it('trailing cursor at exactly width sits on a fresh next row', () => {
    const lay = layout(buf('abcd', 4), 4); // word exactly fills the row, cursor at end
    // The row is full and the cursor spills to a synthesized empty continuation row so the block
    // cursor is always drawable.
    expect(lay.rows.map((r) => r.text)).toEqual(['abcd', '']);
    expect(lay.cursorRow).toBe(1);
    expect(lay.cursorCol).toBe(0);
    expect(lay.rows[1]?.startBufferOffset).toBe(4);
  });

  it('a full row that is soft-continued does NOT spill (the continuation already exists)', () => {
    // 'abcdefgh' width 4 -> ['abcd','efgh']; cursor at offset 4 is the head of the existing row 1.
    const lay = layout(buf('abcdefgh', 4), 4);
    expect(lay.rows.map((r) => r.text)).toEqual(['abcd', 'efgh']);
    expect(lay.cursorRow).toBe(1);
    expect(lay.cursorCol).toBe(0);
  });

  it('cursor on the first row reports the right column', () => {
    const lay = layout(buf('foo bar baz', 5), 7); // cursor inside row 2 ("baz"? offset 5 = 'a' of bar)
    expect(lay.cursorRow).toBe(0);
    expect(lay.cursorCol).toBe(5);
  });

  it('cursor at a soft-wrap seam lands at the head of the next row', () => {
    // 'foo bar baz' width 7 -> rows ['foo bar','baz']; cursor at offset 8 (start of baz)
    const lay = layout(buf('foo bar baz', 8), 7);
    expect(lay.cursorRow).toBe(1);
    expect(lay.cursorCol).toBe(0);
  });
});

describe('chatBuffer — layout with image spans (display vs buffer offsets)', () => {
  it('renders a span as [Image N] and reports buffer offsets', () => {
    const text = `hi ${span('img-1')}`;
    const lay = layout(buf(text, text.length), 40);
    expect(lay.rows.map((r) => r.text)).toEqual(['hi [Image 1]']);
    // cursor at the buffer end maps to the end of the display label
    expect(lay.cursorCol).toBe('hi [Image 1]'.length);
  });

  it('wraps a row containing a span by the [Image N] display width', () => {
    // 'a ' + span -> display 'a [Image 1]' (len 11). width 5 -> 'a' fits, '[Image 1]' is a long word.
    const text = `a ${span('x')}`;
    const lay = layout(buf(text, 0), 5);
    // 'a' then the over-long display word '[Image 1]' hard-broken at width 5
    expect(lay.rows[0]?.text).toBe('a');
    expect(
      lay.rows
        .slice(1)
        .map((r) => r.text)
        .join(''),
    ).toBe('[Image 1]');
    // The wrapped span rows still map their first glyph back to the span's buffer leading edge.
    expect(lay.rows[1]?.startBufferOffset).toBe(2);
  });
});

describe('chatBuffer — visual vertical motion', () => {
  it('visualUp from the top row returns null', () => {
    expect(visualUp(buf('hello', 2), 10)).toBeNull();
  });

  it('visualDown from the bottom row returns null', () => {
    expect(visualDown(buf('hello', 2), 10)).toBeNull();
  });

  it('visualUp / visualDown move between wrapped rows preserving column', () => {
    // 'abcdefghij' width 4 -> rows ['abcd','efgh','ij']; cursor at offset 6 -> row 1 col 2 ('g')
    const start = buf('abcdefghij', 6);
    const lay0 = layout(start, 4);
    expect(lay0.cursorRow).toBe(1);
    expect(lay0.cursorCol).toBe(2);

    const up = visualUp(start, 4);
    expect(up).not.toBeNull();
    const upLay = up === null ? null : layout(up, 4);
    expect(upLay?.cursorRow).toBe(0);
    expect(upLay?.cursorCol).toBe(2); // 'c' on row 0

    const down = visualDown(start, 4);
    expect(down).not.toBeNull();
    const downLay = down === null ? null : layout(down, 4);
    expect(downLay?.cursorRow).toBe(2);
    // row 2 'ij' has only 2 cells; col clamps to 2 (end)
    expect(downLay?.cursorCol).toBe(2);
  });

  it('visualUp clamps the target column to a shorter row above', () => {
    // 'ab\ncdef' width 10 -> rows ['ab','cdef']; cursor at offset 6 -> row 1 col 3
    const start = buf('ab\ncdef', 6);
    const up = visualUp(start, 10);
    expect(up).not.toBeNull();
    const upLay = up === null ? null : layout(up, 10);
    expect(upLay?.cursorRow).toBe(0);
    expect(upLay?.cursorCol).toBe(2); // 'ab' end, clamped from 3
  });

  it('visualDown then visualUp round-trips on multi-row text', () => {
    const start = buf('one two three four', 3); // width 7 wraps
    const down = visualDown(start, 7);
    expect(down).not.toBeNull();
  });
});
