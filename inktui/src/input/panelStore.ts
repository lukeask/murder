/**
 * `panelStore` — the visible-panel set as the app's view state.
 *
 * View state is a `Set<PanelId>` of toggled-on panels, **not** a `_view` enum (the old
 * stringly-typed anti-pattern that forced one-view-at-a-time and a re-home bug class). Panels are
 * independent toggles: `ctrl+<n>` flips one on/off, and several can be on at once (left + right
 * regions visible together). The store holds *only* which panels are visible; it knows nothing
 * about focus — the re-home invariant lives in {@link ./focusStore.js}, derived from this set.
 *
 * Framework-agnostic vanilla Zustand (rule 4): no React import, so a future web/phone shell binds
 * it the same way the Ink hook does. The set is exposed read-only; mutation is only through the
 * actions, which ref-swap a *new* set each time so a `useStore(s => s.visible)` subscriber re-renders
 * on change (a mutated-in-place Set would keep identity and never notify).
 */

import { createStore, type StoreApi } from 'zustand/vanilla';
import type { PanelId } from './panels.js';

/** The panel store's state: the set of visible panels plus the verbs that mutate it. */
export interface PanelState {
  /** The toggled-on panels. Read-only to callers; replaced wholesale by the actions on change. */
  readonly visible: ReadonlySet<PanelId>;
  /** Toggle a panel: visible → hidden, hidden → visible. The `ctrl+<n>` primitive. */
  toggle(id: PanelId): void;
  /** Ensure a panel is visible (idempotent). Used when `ctrl+<n>` must *bring up* a hidden panel
   * before focusing it — the plan's "toggling it on if currently off". */
  show(id: PanelId): void;
  /** Ensure a panel is hidden (idempotent). */
  hide(id: PanelId): void;
}

/** Create a panel store, optionally seeded with an initial visible set (tests seed it; the app
 * starts empty and the shell decides defaults). Returns the vanilla handle. */
export function createPanelStore(initialVisible: Iterable<PanelId> = []): StoreApi<PanelState> {
  return createStore<PanelState>()((set) => ({
    visible: new Set(initialVisible),
    toggle(id) {
      set((state) => {
        const next = new Set(state.visible);
        if (next.has(id)) {
          next.delete(id);
        } else {
          next.add(id);
        }
        return { visible: next };
      });
    },
    show(id) {
      set((state) => {
        if (state.visible.has(id)) {
          return state; // already visible — keep identity so no spurious re-render
        }
        return { visible: new Set(state.visible).add(id) };
      });
    },
    hide(id) {
      set((state) => {
        if (!state.visible.has(id)) {
          return state;
        }
        const next = new Set(state.visible);
        next.delete(id);
        return { visible: next };
      });
    },
  }));
}

/** The panel store handle type, re-exported so callers don't reach into `zustand/vanilla`. */
export type PanelStoreApi = StoreApi<PanelState>;
