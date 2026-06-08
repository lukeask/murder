/**
 * The keymap registry — where panels' *declared* keymaps live so the one root dispatcher can find
 * the focused panel's keys (rule 5). A panel registers its {@link PanelKeymap} under its
 * {@link PanelId} when it mounts and drops it when it unmounts; the dispatcher reads the focused
 * panel's entry and nothing else. This is what replaces the old central key table: the table is now
 * *assembled from what panels declare*, never hand-edited centrally.
 *
 * Framework-agnostic vanilla Zustand (rule 4): the React registration hook lives in `src/hooks/`.
 * The value is a plain `Record<PanelId, PanelKeymap>`; ref-swapped on register/unregister so a
 * subscriber (the dispatcher's wiring hook) sees the change.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';
import type { PanelKeymap } from './keymap.js';
import type { PanelId } from './panels.js';

/** The registry state: the declared keymaps by panel, plus register/unregister verbs. */
export interface KeymapRegistryState {
  /** The currently registered panel keymaps. Read-only to callers; replaced on change. */
  readonly keymaps: Partial<Record<PanelId, PanelKeymap>>;
  /** Register (or replace) a panel's declared keymap + intent handler. */
  register(id: PanelId, keymap: PanelKeymap): void;
  /** Remove a panel's keymap (on unmount). Idempotent. */
  unregister(id: PanelId): void;
}

/** Create a keymap registry store. */
export function createKeymapRegistry(): KeymapRegistryApi {
  return createStore<KeymapRegistryState>()((set) => ({
    keymaps: {},
    register(id, keymap) {
      set((state) => ({ keymaps: { ...state.keymaps, [id]: keymap } }));
    },
    unregister(id) {
      set((state) => {
        if (state.keymaps[id] === undefined) {
          return state;
        }
        const next = { ...state.keymaps };
        delete next[id];
        return { keymaps: next };
      });
    },
  }));
}

/** The registry handle type, re-exported so callers don't import `zustand/vanilla`. */
export type KeymapRegistryApi = StoreApi<KeymapRegistryState>;
