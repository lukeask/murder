/**
 * Pure offset math for the workspace slide (workspaces plan, step 4b) — the eased window position
 * over the concatenated double-height frame, without timers or a render (the plan's testing note:
 * pure-function coverage; no need to test the interval loop).
 */

import { describe, expect, it } from 'vitest';
import {
  slideDone,
  slideRowOffset,
  WORKSPACE_SLIDE_MS,
} from '../../src/components/WorkspaceSlideOverlay.js';

const ROWS = 24;
const START = 1_000;

describe('slideRowOffset (cookbook)', () => {
  it('next (J): starts at 0 and eases to rows', () => {
    expect(slideRowOffset('next', START, START, ROWS)).toBe(0);
    expect(slideRowOffset('next', START, START + WORKSPACE_SLIDE_MS, ROWS)).toBe(ROWS);
  });

  it('prev (K): starts at rows and eases to 0', () => {
    expect(slideRowOffset('prev', START, START, ROWS)).toBe(ROWS);
    expect(slideRowOffset('prev', START, START + WORKSPACE_SLIDE_MS, ROWS)).toBe(0);
  });

  it('eases out: past halfway before half the duration has elapsed', () => {
    const midway = slideRowOffset('next', START, START + WORKSPACE_SLIDE_MS / 2, ROWS);
    expect(midway).toBeGreaterThan(ROWS / 2);
    expect(midway).toBeLessThan(ROWS);
  });

  it('is monotonic per direction across the whole slide', () => {
    let previous = -1;
    for (let t = 0; t <= WORKSPACE_SLIDE_MS; t += 10) {
      const offset = slideRowOffset('next', START, START + t, ROWS);
      expect(offset).toBeGreaterThanOrEqual(previous);
      previous = offset;
    }
  });
});

describe('slideRowOffset (edge cases)', () => {
  it('clamps past the end (never overshoots the target frame)', () => {
    expect(slideRowOffset('next', START, START + WORKSPACE_SLIDE_MS * 3, ROWS)).toBe(ROWS);
    expect(slideRowOffset('prev', START, START + WORKSPACE_SLIDE_MS * 3, ROWS)).toBe(0);
  });

  it('clamps a now before startedAt (clock skew) to the start position', () => {
    expect(slideRowOffset('next', START, START - 500, ROWS)).toBe(0);
    expect(slideRowOffset('prev', START, START - 500, ROWS)).toBe(ROWS);
  });

  it('slideDone flips exactly at the duration boundary', () => {
    expect(slideDone(START, START + WORKSPACE_SLIDE_MS - 1)).toBe(false);
    expect(slideDone(START, START + WORKSPACE_SLIDE_MS)).toBe(true);
  });
});
