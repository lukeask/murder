/**
 * `useThemeCssVars` — subscribe to the process-global theme store and write the active theme's CSS
 * custom properties onto `:root` whenever the scheme changes. Mount this once near the app root.
 *
 * The theme store (`@core/theme/themeStore`) is the same singleton the Ink UI uses, so a future
 * theme-switcher works identically on web: call `setTheme(id)` (or commit through the settings
 * slice) and these vars repaint the whole DOM. Components read the vars from CSS; nothing in React
 * touches hex values.
 */

import { useTheme } from '@core/theme/themeStore.js';
import { useEffect } from 'react';
import { applyThemeCssVars } from './cssVars.js';

export function useThemeCssVars(): void {
  const theme = useTheme();
  useEffect(() => {
    applyThemeCssVars(theme);
  }, [theme]);
}
