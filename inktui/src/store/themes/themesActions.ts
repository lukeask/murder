/**
 * Themes actions — the *only* code that calls the bus for the theme registry (rule 3).
 *
 * RPCs mirror `spawn_favorites` / Python `user_config.py`:
 *  - `tui.load_themes` — load persisted yaml registry + register palettes.
 *  - `tui.save_themes` — persist whole list; echo normalized list.
 *  - `tui.import_theme` — validate JSON paste, append, save.
 */

import type { StoreApi } from 'zustand';
import type { ApplicationClient } from '../../application/ApplicationClient.js';
import { asCommandResult, asQueryResult } from '../../application/resultCast.js';
import { applyThemeRecords, type ThemeRecord } from '../../theme/palettes.js';
import type { AppStore } from '../store.js';
import { toastStore } from '../toast/toastStore.js';



export interface ThemesActions {
  load(): Promise<void>;
  save(themes: readonly ThemeRecord[]): Promise<void>;
  importTheme(json: string, id?: string): Promise<string>;
  remove(id: string): Promise<void>;
}

function toItems(themes: readonly ThemeRecord[] | undefined): readonly ThemeRecord[] {
  return themes ?? [];
}

export function createThemesActions(bus: ApplicationClient, store: StoreApi<AppStore>): ThemesActions {
  async function commit(next: readonly ThemeRecord[]): Promise<void> {
    applyThemeRecords(next);
    store.setState((state) => ({
      themes: { ...state.themes, items: next, status: 'ready', error: null },
    }));
    try {
      const reply = await bus.command('themes.set', { themes: next });
      const saved = toItems(
        asCommandResult<'themes.set', { themes?: readonly ThemeRecord[] }>(reply).themes,
      );
      applyThemeRecords(saved);
      store.setState({ themes: { items: saved, status: 'ready', error: null } });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      store.setState((state) => ({ themes: { ...state.themes, error: message } }));
      toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
    }
  }

  return {
    async load(): Promise<void> {
      store.setState((state) => ({ themes: { ...state.themes, status: 'loading' } }));
      try {
        const reply = await bus.query('themes.get', {});
        const items = toItems(
          asQueryResult<'themes.get', { themes?: readonly ThemeRecord[] }>(reply).themes,
        );
        applyThemeRecords(items);
        store.setState({ themes: { items, status: 'ready', error: null } });
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => ({
          themes: { ...state.themes, status: 'error', error: message },
        }));
      }
    },

    async save(themes: readonly ThemeRecord[]): Promise<void> {
      await commit(themes);
    },

    async importTheme(json: string, id?: string): Promise<string> {
      const reply = await bus.command('theme.import', {
        json,
        ...(id === undefined ? {} : { id }),
      });
      const result = asCommandResult<'theme.import', { themes?: readonly ThemeRecord[]; id: string }>(
        reply,
      );
      const items = toItems(result.themes);
      applyThemeRecords(items);
      store.setState({ themes: { items, status: 'ready', error: null } });
      return result.id;
    },

    async remove(id: string): Promise<void> {
      const next = store.getState().themes.items.filter((theme) => theme.id !== id);
      await commit(next);
    },
  };
}
