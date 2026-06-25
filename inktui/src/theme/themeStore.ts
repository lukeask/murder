/**
 * themeStore — the live UI color scheme: the selected {@link ThemeId} and the {@link Theme} built
 * from its palette.
 *
 * Unlike the per-context input stores, the theme is process-global (one scheme paints the whole UI),
 * so this is a module-level singleton vanilla-Zustand store. Components read it via {@link useTheme}
 * (re-render on change); non-React call sites (pure helpers like `paneColors`, selectors) read the
 * current value via {@link getTheme} or — preferred — take the theme as a parameter so the call site
 * stays decoupled from this store.
 *
 * Default scheme is {@link DEFAULT_THEME_ID} (everforest-dark), so the UI looks identical to before
 * runtime switching existed.
 *
 * ## Source of truth: persisted scheme = `settingsSlice.theme`; this store = what's painted now
 * `settings.theme` (see `../store/settings/settingsSlice.ts`) is the source of truth for the
 * *persisted/committed* scheme. This `themeStore` holds the scheme *currently painted*, which is
 * normally a mirror of `settings.theme` but may transiently diverge during a live preview. It is
 * process-global so non-React code can read the palette synchronously (via {@link getTheme}).
 *
 * Two sanctioned callers of {@link setTheme}:
 *   1. The Shell's settings→theme bridge (`../components/App.tsx`) commits the persisted value:
 *      when `settings.theme` changes it pushes that id here (validated against the theme registry).
 *   2. `SettingsModal` (`../components/SettingsModal.tsx`) *previews* a browsed theme by calling
 *      {@link setTheme} directly — deliberately bypassing settings so the preview stays transient.
 *      It commits through `actions.settings.update` on Save (which flows back via caller 1) and
 *      reverts with `setTheme(persistedTheme)` on cancel or when the cursor leaves the theme rows.
 *
 * So: to *change the persisted scheme*, write through `actions.settings.update`, never `setTheme`.
 * Direct {@link setTheme} is reserved for the transient preview path above.
 */

import { useStoreWithEqualityFn } from 'zustand/traditional';
import { createStore, type StoreApi } from 'zustand/vanilla';
import { buildTheme, type Theme } from './buildTheme.js';
import {
  DEFAULT_THEME_ID,
  getPalette,
  getThemeMeta,
  hasTheme,
  type ThemeId,
} from './palettes.js';

/** The theme store's state: the selected scheme id, its built theme, and the setter. */
export interface ThemeState {
  /** The selected scheme. */
  readonly id: ThemeId;
  /** The semantic theme built from `id`'s palette. Replaced whenever `id` changes. */
  readonly theme: Theme;
  /** Select a scheme and rebuild `theme`. No-op-cheap: a fresh `theme` object identity on change. */
  setTheme(id: ThemeId): void;
}

/** The handle type, re-exported so callers needn't import `zustand/vanilla`. */
export type ThemeStoreApi = StoreApi<ThemeState>;

function resolveThemePaint(id: ThemeId): { id: ThemeId; theme: Theme } {
  const resolvedId = hasTheme(id) ? id : DEFAULT_THEME_ID;
  const palette = getPalette(resolvedId) ?? getPalette(DEFAULT_THEME_ID);
  if (palette === undefined) {
    throw new Error('theme registry missing default palette');
  }
  const variant = getThemeMeta(resolvedId)?.variant ?? 'dark';
  return { id: resolvedId, theme: buildTheme(palette, variant) };
}

/** Build the store seeded at `id`. Factored out for tests (a fresh, isolated store per case). */
export function createThemeStore(id: ThemeId = DEFAULT_THEME_ID): ThemeStoreApi {
  const initial = resolveThemePaint(id);
  return createStore<ThemeState>()((set) => ({
    id: initial.id,
    theme: initial.theme,
    setTheme(next) {
      set(resolveThemePaint(next));
    },
  }));
}

/** The process-global theme store. */
export const themeStore: ThemeStoreApi = createThemeStore();

/** Select the live scheme. Mutates the global store; subscribers (`useTheme`) re-render. */
export function setTheme(id: ThemeId): void {
  themeStore.getState().setTheme(id);
}

/**
 * Escape hatch for non-React call sites (pure helpers, selectors): the current semantic theme. Reads
 * a snapshot — it does NOT subscribe, so a caller that needs to react to scheme changes must use
 * {@link useTheme} or take the theme as a parameter instead.
 */
export function getTheme(): Theme {
  return themeStore.getState().theme;
}

/** The live semantic theme. Re-renders the calling component when the scheme changes. */
export function useTheme(): Theme {
  return useStoreWithEqualityFn(themeStore, (s) => s.theme);
}

/** The live scheme id (for the settings menu's current-selection indicator). */
export function useThemeId(): ThemeId {
  return useStoreWithEqualityFn(themeStore, (s) => s.id);
}
