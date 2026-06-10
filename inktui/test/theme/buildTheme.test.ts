/**
 * `buildTheme` test — the semantic-role mapping is the contract every palette must satisfy, so we
 * snapshot the full role→hex table for each registered palette. The snapshot freezes both that the
 * role set is identical across schemes AND the per-palette surface choices (a light scheme must NOT
 * resolve its selection/header bands to the same near-white slots a dark scheme uses — those would
 * vanish on paper). A regression in either shows up as a snapshot diff.
 */

import { describe, expect, it } from 'vitest';
import { buildTheme } from '../../src/theme/buildTheme.js';
import { PALETTES, type ThemeId } from '../../src/theme/palettes.js';

const ids = Object.keys(PALETTES) as ThemeId[];

describe('buildTheme — per-palette role mapping', () => {
  it.each(ids)('snapshots the semantic theme for %s', (id) => {
    expect(buildTheme(PALETTES[id], id)).toMatchSnapshot();
  });

  it('produces the same role set for every palette', () => {
    const roleSets = ids.map((id) => Object.keys(buildTheme(PALETTES[id], id)).sort());
    for (const roles of roleSets) {
      expect(roles).toEqual(roleSets[0]);
    }
  });

  it('keeps light-scheme surface bands off the near-white base slots (visible on paper)', () => {
    const light = buildTheme(PALETTES['everforest-light'], 'everforest-light');
    const paper = PALETTES['everforest-light'].bg0;
    // The selection/header bands must be distinct from the brightest paper tone, or they disappear.
    expect(light.panelHeaderBg).not.toBe(paper);
    expect(light.panelSelectedBg).not.toBe(paper);
    expect(light.rowSelectedBg).not.toBe(paper);
  });
});
