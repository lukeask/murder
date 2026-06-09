/**
 * useOrientation test — the pure threshold predicate (the load-bearing decision).
 *
 * The hook itself is thin glue over `useTerminalSize`; the boundary logic lives in `isPortrait` /
 * `orientationFor`, which are tested here without a terminal. Covers the portrait/landscape boundary
 * at the default ~2.2 aspect plus an explicit custom aspect.
 */

import { describe, expect, it } from 'vitest';
import { isPortrait, ORIENTATION_ASPECT, orientationFor } from '../../src/hooks/useOrientation.js';

describe('isPortrait — aspect threshold', () => {
  it('defaults to ~2.2 so a roughly-square window is landscape', () => {
    expect(ORIENTATION_ASPECT).toBeCloseTo(2.2);
  });

  it('is portrait when columns < rows * ASPECT', () => {
    // rows=40 → threshold 88 columns. 80 columns is below it → portrait.
    expect(isPortrait(80, 40)).toBe(true);
  });

  it('is landscape when columns >= rows * ASPECT', () => {
    // rows=40 → threshold 88 columns. 120 columns is above it → landscape.
    expect(isPortrait(120, 40)).toBe(false);
  });

  it('treats a wide standard 80x24 terminal as landscape', () => {
    // rows=24 → threshold 52.8 columns; 80 columns clears it.
    expect(isPortrait(80, 24)).toBe(false);
  });

  it('treats a tall narrow split (40x50) as portrait', () => {
    // rows=50 → threshold 110 columns; 40 columns is well below.
    expect(isPortrait(40, 50)).toBe(true);
  });

  it('sits exactly on the boundary as landscape (strict <)', () => {
    // columns === rows*aspect is NOT portrait (predicate is `<`).
    expect(isPortrait(100, 50, 2.0)).toBe(false);
    expect(isPortrait(99, 50, 2.0)).toBe(true);
  });

  it('honours a custom aspect override', () => {
    // With aspect 1.0 the comparison is the bare columns < rows.
    expect(isPortrait(30, 40, 1.0)).toBe(true);
    expect(isPortrait(50, 40, 1.0)).toBe(false);
  });
});

describe('orientationFor', () => {
  it('maps a size to the named orientation', () => {
    expect(orientationFor({ columns: 80, rows: 40 })).toBe('portrait');
    expect(orientationFor({ columns: 200, rows: 40 })).toBe('landscape');
  });
});
