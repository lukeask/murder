/**
 * theme — DEPRECATED thin re-export. Kept so stragglers and tests that still import the static dark
 * `theme` keep compiling. The color system now lives under `src/theme/`:
 *
 *  - `theme/palettes.ts`  — raw named schemes + {@link ThemeId}.
 *  - `theme/buildTheme.ts`— the semantic role mapping, parameterized over a palette.
 *  - `theme/themeStore.ts`— the runtime-selectable scheme.
 *
 * Prefer `useTheme()` (React components) or `getTheme()` / a passed `Theme` parameter (non-React
 * call sites). The `theme` exported here is a STATIC everforest-dark build that never tracks the
 * user's runtime selection — do not reach for it in new code.
 */

import { buildTheme } from './theme/buildTheme.js';
import { PALETTES } from './theme/palettes.js';

export type { Theme } from './theme/buildTheme.js';
export { buildTheme } from './theme/buildTheme.js';
export type { Palette, ThemeId } from './theme/palettes.js';
export { everforestDarkHard as palette } from './theme/palettes.js';
export { getTheme, setTheme, useTheme } from './theme/themeStore.js';

/** @deprecated Static everforest-dark theme. Use `useTheme()` / `getTheme()` instead. */
export const theme = buildTheme(PALETTES['everforest-dark'], 'everforest-dark');
