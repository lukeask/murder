/**
 * cssVars tests: the Theme → CSS-custom-property mapping (kebab-casing, full role coverage) and the
 * DOM write. This is the styling contract the whole web UI depends on, so it is pinned here.
 */

import { buildTheme } from '@core/theme/buildTheme.js';
import { DEFAULT_THEME_ID, PALETTES } from '@core/theme/palettes.js';
import { describe, expect, it } from 'vitest';
import { applyThemeCssVars, themeToCssVars } from '../src/theme/cssVars.js';

const theme = buildTheme(PALETTES[DEFAULT_THEME_ID], DEFAULT_THEME_ID);

describe('themeToCssVars', () => {
  it('maps every semantic role to a kebab-cased --color-* variable', () => {
    const vars = themeToCssVars(theme);
    // One var per role.
    expect(Object.keys(vars)).toHaveLength(Object.keys(theme).length);
    // camelCase → kebab-case.
    expect(vars['--color-row-selected-bg']).toBe(theme.rowSelectedBg);
    expect(vars['--color-gauge-label-text']).toBe(theme.gaugeLabelText);
    expect(vars['--color-text']).toBe(theme.text);
    // Every value is a hex string.
    for (const value of Object.values(vars)) {
      expect(value).toMatch(/^#[0-9a-fA-F]{6}$/);
    }
  });
});

describe('applyThemeCssVars', () => {
  it('writes the variables onto the given root element', () => {
    const root = document.createElement('div');
    applyThemeCssVars(theme, root);
    expect(root.style.getPropertyValue('--color-text')).toBe(theme.text);
    expect(root.style.getPropertyValue('--color-focus')).toBe(theme.focus);
    expect(root.style.getPropertyValue('--color-error')).toBe(theme.error);
  });
});
