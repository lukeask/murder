/**
 * SettingsPanel theme switch: clicking a theme swatch calls setTheme on the shared themeStore, which
 * (via useThemeCssVars, mounted here) repaints the `--color-*` CSS variables on :root. We mount the
 * theme bridge alongside the panel, click the light theme, and assert a known CSS var changed to the
 * light palette's value. Also confirms the swatch reflects the active id.
 */

import { buildTheme } from '@core/theme/buildTheme.js';
import { getPalette } from '@core/theme/palettes.js';
import { setTheme } from '@core/theme/themeStore.js';
import { fireEvent, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { SettingsPanel } from '../src/components/panels/SettingsPanel.js';
import { useThemeCssVars } from '../src/theme/useThemeCssVars.js';
import { makeStore, renderWithStore } from './helpers.js';

function Harness(): React.JSX.Element {
  useThemeCssVars();
  return <SettingsPanel />;
}

afterEach(() => {
  cleanup();
  setTheme('everforest-dark');
});

describe('SettingsPanel theme switch', () => {
  it('repaints :root CSS vars when a theme is chosen', () => {
    const { store, bus } = makeStore();
    bus.stubRpc('tui.load_themes', { ok: true, themes: [] });
    renderWithStore(<Harness />, { store, bus });

    const lightPalette = getPalette('everforest-light')!;
    const lightText = buildTheme(lightPalette, 'light').text;
    fireEvent.click(screen.getByText('Everforest Light'));

    expect(document.documentElement.style.getPropertyValue('--color-text')).toBe(lightText);
  });

  it('reflects the active scheme onto <html data-theme> so DS components switch', () => {
    const { store, bus } = makeStore();
    bus.stubRpc('tui.load_themes', { ok: true, themes: [] });
    renderWithStore(<Harness />, { store, bus });

    expect(document.documentElement.dataset['theme']).toBe('dark');

    fireEvent.click(screen.getByText('Everforest Light'));
    expect(document.documentElement.dataset['theme']).toBe('light');
  });

  it('marks the active theme swatch', () => {
    setTheme('everforest-light');
    const { store, bus } = makeStore();
    bus.stubRpc('tui.load_themes', { ok: true, themes: [] });
    renderWithStore(<Harness />, { store, bus });
    const swatch = screen.getByText('Everforest Light').closest('.theme-swatch');
    expect(swatch?.getAttribute('data-on')).toBe('true');
  });
});
