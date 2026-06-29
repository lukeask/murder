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
import type { FocusId } from './focusStore.js';
import type { PanelKeymap } from './keymap.js';

/** The registry state: the declared keymaps by focusable, plus register/unregister verbs.
 *
 * ## Phase 4a — keyed by {@link FocusId}, not {@link PanelId}
 * The dispatcher routes a matched key to `keymaps[focusedId]`, where `focusedId` is now a `FocusId`
 * (a panel, chat, or a mounted Stage pane). Keying the registry by `FocusId` lets a focusable Stage
 * pane declare its own keymap (e.g. `j`/`k` to scroll its history) the same
 * way a panel does — `usePanelKeymap` passes any `FocusId`. Panels still pass a `PanelId` (a `FocusId`
 * subtype), so every existing caller is unchanged; chat declares nothing (it has the persistent
 * chat-input handler, not a registry entry). */
export interface KeymapRegistryState {
  /** The currently registered keymaps, keyed by focusable. Read-only to callers; replaced on change. */
  readonly keymaps: Partial<Record<FocusId, PanelKeymap>>;
  /** Register (or replace) a focusable's declared keymap + intent handler. */
  register(id: FocusId, keymap: PanelKeymap): void;
  /** Remove a focusable's keymap (on unmount). Idempotent. */
  unregister(id: FocusId): void;
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
