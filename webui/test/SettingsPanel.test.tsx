/**
 * SettingsPanel theme switch: clicking a theme swatch calls setTheme on the shared themeStore, which
 * (via useThemeCssVars, mounted here) repaints the `--color-*` CSS variables on :root. We mount the
 * theme bridge alongside the panel, click the light theme, and assert a known CSS var changed to the
 * light palette's value. Also confirms the swatch reflects the active id.
 */

import { buildTheme } from '@core/theme/buildTheme.js';
import { PALETTES } from '@core/theme/palettes.js';
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
  setTheme('everforest-dark'); // reset the process-global theme between tests
});

describe('SettingsPanel theme switch', () => {
  it('repaints :root CSS vars when a theme is chosen', () => {
    const { store } = makeStore();
    renderWithStore(<Harness />, { store });

    const lightText = buildTheme(PALETTES['everforest-light'], 'everforest-light').text;
    fireEvent.click(screen.getByText('everforest-light'));

    expect(document.documentElement.style.getPropertyValue('--color-text')).toBe(lightText);
  });

  it('reflects the active scheme onto <html data-theme> so DS components switch', () => {
    const { store } = makeStore();
    renderWithStore(<Harness />, { store });

    // Default scheme (everforest-dark) → data-theme="dark".
    expect(document.documentElement.dataset['theme']).toBe('dark');

    fireEvent.click(screen.getByText('everforest-light'));
    expect(document.documentElement.dataset['theme']).toBe('light');
  });

  it('marks the active theme swatch', () => {
    setTheme('everforest-light');
    const { store } = makeStore();
    renderWithStore(<Harness />, { store });
    // The label text lives inside the swatch button; the inspectable active marker (`data-on`) sits
    // on the button itself, so read it off the enclosing toggle.
    const swatch = screen.getByText('everforest-light').closest('.theme-swatch');
    expect(swatch?.getAttribute('data-on')).toBe('true');
  });
});
