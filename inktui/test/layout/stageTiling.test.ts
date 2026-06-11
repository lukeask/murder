/**
 * stageTiling tests — the pure doc/chat split + chat-grid geometry that drives the Stage layout.
 *
 * Covers the plan's stated intuitions (landscape, one document) and the portrait stacking rule, plus
 * the chats-only fallback and the chunking math. No React/Ink — the geometry is a pure function.
 */

import { describe, expect, it } from 'vitest';
import {
  chatGridColumns,
  chunkRows,
  computeStageLayout,
  regionWeights,
} from '../../src/layout/stageTiling.js';

describe('chatGridColumns', () => {
  it('uses a single column for 0 or 1 chat', () => {
    expect(chatGridColumns(0, true, 'landscape')).toBe(1);
    expect(chatGridColumns(1, false, 'landscape')).toBe(1);
  });

  it('landscape WITH a doc: stacks (1 col) up to 3 chats, then goes 2-wide at 4 (the 2×2 grid)', () => {
    expect(chatGridColumns(2, true, 'landscape')).toBe(1);
    expect(chatGridColumns(3, true, 'landscape')).toBe(1);
    expect(chatGridColumns(4, true, 'landscape')).toBe(2);
  });

  it('landscape WITHOUT a doc: 2 side by side, then 2 cols to 6, 3 cols beyond', () => {
    expect(chatGridColumns(2, false, 'landscape')).toBe(2);
    expect(chatGridColumns(3, false, 'landscape')).toBe(2);
    expect(chatGridColumns(6, false, 'landscape')).toBe(2);
    expect(chatGridColumns(7, false, 'landscape')).toBe(3);
  });

  it('portrait always stacks in a single column', () => {
    expect(chatGridColumns(4, true, 'portrait')).toBe(1);
    expect(chatGridColumns(6, false, 'portrait')).toBe(1);
  });
});

describe('regionWeights', () => {
  it('doc gets weight 0 when no doc, the chat region weight 0 when no chats', () => {
    expect(regionWeights(3, false)).toEqual({ doc: 0, chat: 1 });
    expect(regionWeights(0, true)).toEqual({ doc: 1, chat: 0 });
  });

  it('splits the Stage evenly until the chat grid needs the room (≥4 chats → doc yields to a third)', () => {
    expect(regionWeights(1, true)).toEqual({ doc: 1, chat: 1 }); // 50/50
    expect(regionWeights(3, true)).toEqual({ doc: 1, chat: 1 }); // 50/50
    expect(regionWeights(4, true)).toEqual({ doc: 1, chat: 2 }); // doc 1/3
  });
});

describe('chunkRows', () => {
  it('packs items left-to-right into rows of `columns`', () => {
    expect(chunkRows([1, 2, 3, 4], 2)).toEqual([
      [1, 2],
      [3, 4],
    ]);
    expect(chunkRows([1, 2, 3], 1)).toEqual([[1], [2], [3]]);
    expect(chunkRows([1, 2, 3], 2)).toEqual([[1, 2], [3]]);
  });

  it('treats columns < 1 as a single column', () => {
    expect(chunkRows([1, 2], 0)).toEqual([[1], [2]]);
  });
});

describe('computeStageLayout — the plan intuitions end-to-end', () => {
  it('1 doc + 1 chat → equal halves, single chat row', () => {
    const l = computeStageLayout(['a'], true, 'landscape');
    expect(l).toEqual({ docWeight: 1, chatWeight: 1, rows: [['a']] });
  });

  it('1 doc + 2 chats → 50/50, chats stacked vertically', () => {
    const l = computeStageLayout(['a', 'b'], true, 'landscape');
    expect(l.docWeight).toBe(1);
    expect(l.chatWeight).toBe(1);
    expect(l.rows).toEqual([['a'], ['b']]);
  });

  it('1 doc + 3 chats → 50/50, three chats stacked', () => {
    const l = computeStageLayout(['a', 'b', 'c'], true, 'landscape');
    expect(l.rows).toEqual([['a'], ['b'], ['c']]);
  });

  it('1 doc + 4 chats → doc 1/3, a 2×2 chat grid', () => {
    const l = computeStageLayout(['a', 'b', 'c', 'd'], true, 'landscape');
    expect(l.docWeight).toBe(1);
    expect(l.chatWeight).toBe(2);
    expect(l.rows).toEqual([
      ['a', 'b'],
      ['c', 'd'],
    ]);
  });

  it('portrait stacks everything in one column', () => {
    const l = computeStageLayout(['a', 'b', 'c', 'd'], true, 'portrait');
    expect(l.rows).toEqual([['a'], ['b'], ['c'], ['d']]);
  });

  it('chats only (no doc) → doc region absent (weight 0)', () => {
    const l = computeStageLayout(['a', 'b'], false, 'landscape');
    expect(l.docWeight).toBe(0);
    expect(l.rows).toEqual([['a', 'b']]);
  });
});
