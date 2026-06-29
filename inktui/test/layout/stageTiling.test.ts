/**
 * stageTiling tests — the pure doc/transcript split + transcript-grid geometry that drives the Stage layout.
 *
 * Covers the plan's stated intuitions (landscape, one document) and the portrait stacking rule, plus
 * the transcripts-only fallback and the chunking math. No React/Ink — the geometry is a pure function.
 */

import { describe, expect, it } from 'vitest';
import {
  chunkRows,
  computeStageLayout,
  regionWeights,
  transcriptGridColumns,
} from '../../src/layout/stageTiling.js';

describe('transcriptGridColumns', () => {
  it('uses a single column for 0 or 1 transcript', () => {
    expect(transcriptGridColumns(0, true, 'landscape')).toBe(1);
    expect(transcriptGridColumns(1, false, 'landscape')).toBe(1);
  });

  it('landscape WITH a doc: stacks (1 col) up to 3 transcripts, then goes 2-wide at 4 (the 2×2 grid)', () => {
    expect(transcriptGridColumns(2, true, 'landscape')).toBe(1);
    expect(transcriptGridColumns(3, true, 'landscape')).toBe(1);
    expect(transcriptGridColumns(4, true, 'landscape')).toBe(2);
  });

  it('landscape WITHOUT a doc: 2 side by side, then 2 cols to 6, 3 cols beyond', () => {
    expect(transcriptGridColumns(2, false, 'landscape')).toBe(2);
    expect(transcriptGridColumns(3, false, 'landscape')).toBe(2);
    expect(transcriptGridColumns(6, false, 'landscape')).toBe(2);
    expect(transcriptGridColumns(7, false, 'landscape')).toBe(3);
  });

  it('portrait always stacks in a single column', () => {
    expect(transcriptGridColumns(4, true, 'portrait')).toBe(1);
    expect(transcriptGridColumns(6, false, 'portrait')).toBe(1);
  });
});

describe('regionWeights', () => {
  it('doc gets weight 0 when no doc, the transcript region weight 0 when no transcripts', () => {
    expect(regionWeights(3, false)).toEqual({ doc: 0, transcript: 1 });
    expect(regionWeights(0, true)).toEqual({ doc: 1, transcript: 0 });
  });

  it('splits the Stage evenly until the transcript grid needs the room (≥4 transcripts → doc yields to a third)', () => {
    expect(regionWeights(1, true)).toEqual({ doc: 1, transcript: 1 }); // 50/50
    expect(regionWeights(3, true)).toEqual({ doc: 1, transcript: 1 }); // 50/50
    expect(regionWeights(4, true)).toEqual({ doc: 1, transcript: 2 }); // doc 1/3
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
  it('1 doc + 1 transcript → equal halves, single transcript row', () => {
    const l = computeStageLayout(['a'], true, 'landscape');
    expect(l).toEqual({ docWeight: 1, transcriptWeight: 1, rows: [['a']] });
  });

  it('1 doc + 2 transcripts → 50/50, transcripts stacked vertically', () => {
    const l = computeStageLayout(['a', 'b'], true, 'landscape');
    expect(l.docWeight).toBe(1);
    expect(l.transcriptWeight).toBe(1);
    expect(l.rows).toEqual([['a'], ['b']]);
  });

  it('1 doc + 3 transcripts → 50/50, three transcripts stacked', () => {
    const l = computeStageLayout(['a', 'b', 'c'], true, 'landscape');
    expect(l.rows).toEqual([['a'], ['b'], ['c']]);
  });

  it('1 doc + 4 transcripts → doc 1/3, a 2×2 transcript grid', () => {
    const l = computeStageLayout(['a', 'b', 'c', 'd'], true, 'landscape');
    expect(l.docWeight).toBe(1);
    expect(l.transcriptWeight).toBe(2);
    expect(l.rows).toEqual([
      ['a', 'b'],
      ['c', 'd'],
    ]);
  });

  it('portrait stacks everything in one column', () => {
    const l = computeStageLayout(['a', 'b', 'c', 'd'], true, 'portrait');
    expect(l.rows).toEqual([['a'], ['b'], ['c'], ['d']]);
  });

  it('transcripts only (no doc) → doc region absent (weight 0)', () => {
    const l = computeStageLayout(['a', 'b'], false, 'landscape');
    expect(l.docWeight).toBe(0);
    expect(l.rows).toEqual([['a', 'b']]);
  });
});
