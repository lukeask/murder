/**
 * `useThemeCssVars` — subscribe to the process-global theme store and write the active theme's CSS
 * custom properties onto `:root` whenever the scheme changes. Mount this once near the app root.
 *
 * The theme store (`@core/theme/themeStore`) is the same singleton the Ink UI uses, so a future
 * theme-switcher works identically on web: call `setTheme(id)` (or commit through the settings
 * slice) and these vars repaint the whole DOM. Components read the vars from CSS; nothing in React
 * touches hex values.
 */

import { useTheme, useThemeId } from '@core/theme/themeStore.js';
import { getThemeMeta } from '@core/theme/palettes.js';
import { useEffect } from 'react';
import { applyThemeCssVars } from './cssVars.js';

export function useThemeCssVars(): void {
  const theme = useTheme();
  const themeId = useThemeId();
  useEffect(() => {
    applyThemeCssVars(theme);
  }, [theme]);
  // In ADDITION to the runtime --color-* injection above (which app.css still depends on), pin the
  // DS light/dark switch by reflecting the active @core scheme onto `<html data-theme>`. The DS
  // tokens (tokens.css) and components (ds.css) key their light overrides off `[data-theme="light"]`
  // (attribute beats the prefers-color-scheme media query on specificity), so the existing
  // SettingsPanel theme control switches DS components too, with zero new UI.
  useEffect(() => {
    const variant = getThemeMeta(themeId)?.variant ?? 'dark';
    document.documentElement.dataset['theme'] = variant === 'light' ? 'light' : 'dark';
  }, [themeId]);
}
