/**
 * Themes slice — the registry of UI color palettes loaded from `~/.config/murder/themes.yaml`.
 *
 * Hand-written (not `listSlice.ts`): loaded via `tui.load_themes`, persisted via `tui.save_themes`,
 * never snapshot-invalidated. On each successful load/save, {@link applyThemeRecords} updates the
 * in-memory palette registry that {@link themeStore} paints from.
 */

import type { StateCreator } from 'zustand';
import type { ThemeRecord } from '../../theme/palettes.js';
import type { AppStore } from '../store.js';

export type { ThemeRecord };

export interface ThemesState {
  readonly items: readonly ThemeRecord[];
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  readonly error: string | null;
}

export const initialThemesState: ThemesState = {
  items: [],
  status: 'idle',
  error: null,
};

export const createThemesSlice: StateCreator<AppStore, [], [], { themes: ThemesState }> = () => ({
  themes: initialThemesState,
});
