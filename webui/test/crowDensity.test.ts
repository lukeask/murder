/** Crow width → density thresholds (TUI CrowsSurface layout parity). */

import { describe, expect, it } from 'vitest';
import {
  CROW_COMPACT_MIN_WIDTH,
  CROW_FULL_MIN_WIDTH,
  crowDensityFromWidth,
  crowShowMeta,
} from '../src/components/panels/crowDensity.js';

describe('crowDensityFromWidth', () => {
  it('routes full → compact → minimal by width', () => {
    expect(crowDensityFromWidth(0)).toBe('full');
    expect(crowDensityFromWidth(CROW_FULL_MIN_WIDTH)).toBe('full');
    expect(crowDensityFromWidth(CROW_FULL_MIN_WIDTH - 1)).toBe('compact');
    expect(crowDensityFromWidth(CROW_COMPACT_MIN_WIDTH)).toBe('compact');
    expect(crowDensityFromWidth(CROW_COMPACT_MIN_WIDTH - 1)).toBe('minimal');
  });
});

describe('crowShowMeta', () => {
  it('hides meta when collapsed or minimal', () => {
    expect(crowShowMeta('full', true)).toBe(true);
    expect(crowShowMeta('compact', true)).toBe(true);
    expect(crowShowMeta('minimal', true)).toBe(false);
    expect(crowShowMeta('full', false)).toBe(false);
  });
});
