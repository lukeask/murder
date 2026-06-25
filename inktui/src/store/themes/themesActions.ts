/**
 * Themes actions — the *only* code that calls the bus for the theme registry (rule 3).
 *
 * RPCs mirror `spawn_favorites` / Python `user_config.py`:
 *  - `tui.load_themes` — load persisted yaml registry + register palettes.
 *  - `tui.save_themes` — persist whole list; echo normalized list.
 *  - `tui.import_theme` — validate JSON paste, append, save.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import { applyThemeRecords, type ThemeRecord } from '../../theme/palettes.js';
import type { AppStore } from '../store.js';
import { toastStore } from '../toast/toastStore.js';

declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    'tui.load_themes': {
      params: Record<string, never>;
      result: { ok: boolean; themes: readonly ThemeRecord[] };
    };
    'tui.save_themes': {
      params: { themes: readonly ThemeRecord[] };
      result: { ok: boolean; themes: readonly ThemeRecord[] };
    };
    'tui.import_theme': {
      params: { json: string; id?: string };
      result: { ok: boolean; themes: readonly ThemeRecord[]; id: string };
    };
  }
}

export interface ThemesActions {
  load(): Promise<void>;
  save(themes: readonly ThemeRecord[]): Promise<void>;
  importTheme(json: string, id?: string): Promise<string>;
  remove(id: string): Promise<void>;
}

function toItems(themes: readonly ThemeRecord[] | undefined): readonly ThemeRecord[] {
  return themes ?? [];
}

export function createThemesActions(bus: BusClient, store: StoreApi<AppStore>): ThemesActions {
  async function commit(next: readonly ThemeRecord[]): Promise<void> {
    applyThemeRecords(next);
    store.setState((state) => ({
      themes: { ...state.themes, items: next, status: 'ready', error: null },
    }));
    try {
      const reply = await bus.rpc('tui.save_themes', { themes: next });
      const saved = toItems(reply.themes);
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
        const reply = await bus.rpc('tui.load_themes', {});
        const items = toItems(reply.themes);
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
      const reply = await bus.rpc('tui.import_theme', { json, id });
      const items = toItems(reply.themes);
      applyThemeRecords(items);
      store.setState({ themes: { items, status: 'ready', error: null } });
      return reply.id;
    },

    async remove(id: string): Promise<void> {
      const next = store.getState().themes.items.filter((theme) => theme.id !== id);
      await commit(next);
    },
  };
}
