/**
 * `buildTheme` test — the semantic-role mapping is the contract every palette must satisfy, so we
 * snapshot the full role→hex table for each registered palette. The snapshot freezes both that the
 * role set is identical across schemes AND the per-palette surface choices (a light scheme must NOT
 * resolve its selection/header bands to the same near-white slots a dark scheme uses — those would
 * vanish on paper). A regression in either shows up as a snapshot diff.
 */

import { describe, expect, it } from 'vitest';
import { buildTheme } from '../../src/theme/buildTheme.js';
import { getPalette, getThemeMeta, listThemeIds } from '../../src/theme/palettes.js';

const ids = listThemeIds();

describe('buildTheme — per-palette role mapping', () => {
  it.each(ids)('snapshots the semantic theme for %s', (id) => {
    const palette = getPalette(id);
    const variant = getThemeMeta(id)?.variant ?? 'dark';
    expect(palette).toBeDefined();
    expect(buildTheme(palette!, variant)).toMatchSnapshot();
  });

  it('produces the same role set for every palette', () => {
    const roleSets = ids.map((id) => {
      const palette = getPalette(id)!;
      const variant = getThemeMeta(id)?.variant ?? 'dark';
      return Object.keys(buildTheme(palette, variant)).sort();
    });
    for (const roles of roleSets) {
      expect(roles).toEqual(roleSets[0]);
    }
  });

  it('keeps light-scheme surface bands off the near-white base slots (visible on paper)', () => {
    const palette = getPalette('everforest-light')!;
    const light = buildTheme(palette, 'light');
    const paper = palette.bg0;
    expect(light.panelHeaderBg).not.toBe(paper);
    expect(light.panelSelectedBg).not.toBe(paper);
    expect(light.rowSelectedBg).not.toBe(paper);
  });
});
