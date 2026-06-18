/**
 * cssVars tests: the Theme → CSS-custom-property mapping (kebab-casing, full role coverage) and the
 * DOM write. This is the styling contract the whole web UI depends on, so it is pinned here.
 */

import { buildTheme } from '@core/theme/buildTheme.js';
import { DEFAULT_THEME_ID, PALETTES } from '@core/theme/palettes.js';
import { setTheme } from '@core/theme/themeStore.js';
import { renderHook, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { applyThemeCssVars, themeToCssVars } from '../src/theme/cssVars.js';
import { useThemeCssVars } from '../src/theme/useThemeCssVars.js';

const theme = buildTheme(PALETTES[DEFAULT_THEME_ID], DEFAULT_THEME_ID);

describe('themeToCssVars', () => {
  it('maps every semantic role to a kebab-cased --color-* variable', () => {
    const vars = themeToCssVars(theme);
    // One var per scalar role (array roles emit a list var + one var per entry, so the count is at
    // least the role count).
    expect(Object.keys(vars).length).toBeGreaterThanOrEqual(Object.keys(theme).length);
    // camelCase → kebab-case.
    expect(vars['--color-row-selected-bg']).toBe(theme.rowSelectedBg);
    expect(vars['--color-gauge-label-text']).toBe(theme.gaugeLabelText);
    expect(vars['--color-text']).toBe(theme.text);
    // Every scalar value is a hex string; list vars are comma-joined hexes.
    for (const [name, value] of Object.entries(vars)) {
      if (name === '--color-lane-colors') {
        expect(value).toMatch(/^#[0-9a-fA-F]{6}(, #[0-9a-fA-F]{6})*$/);
      } else {
        expect(value).toMatch(/^#[0-9a-fA-F]{6}$/);
      }
    }
  });

  it('projects an array role into a list var plus indexed entry vars', () => {
    const vars = themeToCssVars(theme);
    expect(vars['--color-lane-colors-0']).toBe(theme.laneColors[0]);
    expect(vars['--color-lane-colors']).toContain(theme.laneColors[0] as string);
    expect(vars['--color-lane-colors']).toContain(', ');
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

describe('useThemeCssVars data-theme pin', () => {
  afterEach(() => {
    cleanup();
    setTheme('everforest-dark'); // reset the process-global scheme between cases
  });

  it('reflects the active @core scheme onto <html data-theme> (dark default, light on switch)', () => {
    const { rerender } = renderHook(() => useThemeCssVars());
    // Default scheme is everforest-dark → data-theme="dark".
    expect(document.documentElement.dataset['theme']).toBe('dark');

    setTheme('everforest-light');
    rerender();
    expect(document.documentElement.dataset['theme']).toBe('light');

    setTheme('everforest-dark');
    rerender();
    expect(document.documentElement.dataset['theme']).toBe('dark');
  });
});
