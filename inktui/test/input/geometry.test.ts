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
});
