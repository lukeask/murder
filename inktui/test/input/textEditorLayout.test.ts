import { describe, expect, it } from 'vitest';
import { layoutEditor } from '../../src/input/textEditor/layout.js';
import { plainTextProjection } from '../../src/input/textEditor/projection.js';

describe('layoutEditor', () => {
  it('wraps on hard newlines and soft word boundaries', () => {
    const hard = layoutEditor(
      { text: 'one\ntwo', cursor: 0, desiredVisualColumn: null },
      20,
      plainTextProjection,
    );
    expect(hard.rows.map((row) => row.atoms.map((atom) => atom.text).join(''))).toEqual([
      'one',
      'two',
    ]);

    const soft = layoutEditor(
      { text: 'aaaa bbbb cccc', cursor: 0, desiredVisualColumn: null },
      8,
      plainTextProjection,
    );
    expect(soft.rows.length).toBeGreaterThan(1);
    expect(soft.rows.every((row) => row.columns <= 8)).toBe(true);
  });

  it('maps every legal boundary and supports resize without rewriting text', () => {
    const text = 'hello world';
    const narrow = layoutEditor(
      { text, cursor: 6, desiredVisualColumn: null },
      5,
      plainTextProjection,
    );
    const wide = layoutEditor(
      { text, cursor: 6, desiredVisualColumn: null },
      40,
      plainTextProjection,
    );
    expect(narrow.rows.length).toBeGreaterThan(wide.rows.length);
    expect(wide.cursorRow).toBe(0);
    expect(wide.cursorColumn).toBe(6);

    const empty = layoutEditor(
      { text: '', cursor: 0, desiredVisualColumn: null },
      10,
      plainTextProjection,
    );
    expect(empty.rows).toHaveLength(1);
    expect(empty.cursorRow).toBe(0);
    expect(empty.cursorColumn).toBe(0);
  });

  it('adds a synthetic trailing row when the cursor sits past a full final row', () => {
    const layout = layoutEditor(
      { text: 'abcdefghij', cursor: 10, desiredVisualColumn: null },
      10,
      plainTextProjection,
    );
    expect(layout.rows.length).toBe(2);
    expect(layout.cursorRow).toBe(1);
    expect(layout.cursorColumn).toBe(0);
  });

  it('keeps combining graphemes on one visual atom', () => {
    const text = 'a\u0301b';
    const layout = layoutEditor(
      { text, cursor: text.length, desiredVisualColumn: null },
      10,
      plainTextProjection,
    );
    expect(layout.rows[0]?.atoms.some((atom) => atom.text === 'a\u0301')).toBe(true);
    expect(layout.cursorColumn).toBe(2);
  });
});
