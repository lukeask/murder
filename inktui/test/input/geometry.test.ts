/**
 * Geometry kernel tests — the pure `_directional_focus_target` port, driven by hand-written rects
 * (no rendering). Proves nav picks the geometrically-correct neighbour in each direction and stops
 * at the layout edge.
 */

import { describe, expect, it } from 'vitest';
import {
  directionalFocusTarget,
  type FocusCandidate,
  type Rect,
} from '../../src/input/geometry.js';

/** Build a candidate at a given rect. */
function at(id: string, rect: Rect): FocusCandidate<string> {
  return { id, rect };
}

/**
 * A cross layout:
 *      [top]
 * [left][center][right]
 *     [bottom]
 * center is the source; each neighbour is the unique target in its direction.
 */
const center: Rect = { x: 20, y: 10, width: 10, height: 4 };
const left: Rect = { x: 5, y: 10, width: 10, height: 4 };
const right: Rect = { x: 35, y: 10, width: 10, height: 4 };
const top: Rect = { x: 20, y: 2, width: 10, height: 4 };
const bottom: Rect = { x: 20, y: 18, width: 10, height: 4 };

const cross = [
  at('center', center),
  at('left', left),
  at('right', right),
  at('top', top),
  at('bottom', bottom),
];

describe('directionalFocusTarget', () => {
  it('moves to the right neighbour', () => {
    expect(directionalFocusTarget('right', 'center', cross)).toBe('right');
  });

  it('moves to the left neighbour', () => {
    expect(directionalFocusTarget('left', 'center', cross)).toBe('left');
  });

  it('moves to the neighbour above', () => {
    expect(directionalFocusTarget('up', 'center', cross)).toBe('top');
  });

  it('moves to the neighbour below', () => {
    expect(directionalFocusTarget('down', 'center', cross)).toBe('bottom');
  });

  it('returns null at the layout edge (nothing further right of the rightmost)', () => {
    expect(directionalFocusTarget('right', 'right', cross)).toBeNull();
  });

  it('returns null when the current id is not among candidates', () => {
    expect(directionalFocusTarget('right', 'ghost', cross)).toBeNull();
  });

  it('prefers the nearest candidate in the travel direction', () => {
    // Two panels to the right; the closer one wins on primary gap.
    const near: Rect = { x: 32, y: 10, width: 6, height: 4 };
    const far: Rect = { x: 50, y: 10, width: 6, height: 4 };
    const cands = [at('center', center), at('near', near), at('far', far)];
    expect(directionalFocusTarget('right', 'center', cands)).toBe('near');
  });

  it('prefers a cross-axis-overlapping candidate over an equally-distant non-overlapping one', () => {
    // Both start at the same x (same primary gap); the one whose rows overlap the source wins.
    const overlapping: Rect = { x: 35, y: 11, width: 6, height: 4 }; // overlaps center's rows
    const offset: Rect = { x: 35, y: 40, width: 6, height: 4 }; // far on the cross axis
    const cands = [at('center', center), at('offset', offset), at('overlapping', overlapping)];
    expect(directionalFocusTarget('right', 'center', cands)).toBe('overlapping');
  });

  // Phase 2: prove hjkl follows the on-screen geometry in BOTH orientations. The kernel is purely
  // rect-based, so a landscape arrangement (panes side-by-side) and a portrait one (panes stacked)
  // differ only in the hand-fed rects — the same kernel picks the right neighbour for each, which is
  // exactly what makes directional focus adapt for free when the layout reflows on a terminal resize.
  describe('orientation reflow (rect-based, both layouts)', () => {
    it('landscape: panes side-by-side → ctrl+l moves right (and ctrl+h back)', () => {
      // A landscape Body lays the rails out in a row: left Rail | (Stage) | right Rail. Two panes sit
      // side-by-side at the same y, so the right pane is the `right` neighbour of the left pane.
      const leftPane: Rect = { x: 0, y: 0, width: 30, height: 20 };
      const rightPane: Rect = { x: 30, y: 0, width: 30, height: 20 };
      const cands = [at('left', leftPane), at('right', rightPane)];
      expect(directionalFocusTarget('right', 'left', cands)).toBe('right');
      expect(directionalFocusTarget('left', 'right', cands)).toBe('left');
      // No neighbour up/down in a single side-by-side row.
      expect(directionalFocusTarget('down', 'left', cands)).toBeNull();
    });

    it('portrait: panes stacked → ctrl+j moves down (and ctrl+k back)', () => {
      // A portrait Body stacks the rails in a column: top Rail / (Stage) / bottom Rail. Two panes are
      // stacked at the same x, so the lower pane is the `down` neighbour of the upper pane.
      const topPane: Rect = { x: 0, y: 0, width: 60, height: 10 };
      const bottomPane: Rect = { x: 0, y: 10, width: 60, height: 10 };
      const cands = [at('top', topPane), at('bottom', bottomPane)];
      expect(directionalFocusTarget('down', 'top', cands)).toBe('bottom');
      expect(directionalFocusTarget('up', 'bottom', cands)).toBe('top');
      // No neighbour left/right in a single stacked column.
      expect(directionalFocusTarget('right', 'top', cands)).toBeNull();
    });
  });
});
