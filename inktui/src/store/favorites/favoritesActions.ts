/**
 * Favorites actions — the *only* code that calls the bus for starring/favorites (rule 3).
 *
 * Two RPCs, modeled per the bus contract's prefs pair (service B13's V3):
 *  - `tui.load_favorites {}` → `{ ok, favorites: [id,…] }` — load the persisted starred-id list.
 *  - `tui.save_favorites { favorites: [id,…] }` → `{ ok, favorites }` — persist it.
 * Both directions are required (the prefs had to leave `.murder/` in both directions). Declared via
 * a `declare module` augmentation of the shared {@link RpcMethods} registry, so the C1/C2 bus files
 * (`BusClient.ts`/`UdsBusClient.ts`) stay byte-identical — the seam (rule 4). The keys here
 * (`tui.load_favorites`/`tui.save_favorites`) are distinct from every other slice's keys.
 *
 * ## Bus status: MODELED, NOT LIVE
 *
 * Neither method is on the live bus yet — they land with service B13 (V3). Until then, `load`
 * resolves against whatever the `FakeBusClient` stubs, and a live `UdsBusClient` would reject the
 * call. The action routes a rejection into the slice's `error` field (never throws past the action),
 * so a missing live method degrades to "favorites stay at their defaults" rather than crashing.
 *
 * ## Optimistic local-first writes
 *
 * `toggle`/`setStarred` mutate the local `ids` set immediately (a star must feel instant) and THEN
 * fire `tui.save_favorites` with the new list. The local set is the source of truth for the UI; the
 * RPC is persistence. A save rejection sets `error` but does NOT roll back the local set — the
 * user's intent stands for the session; a reconnect re-loads from the persisted truth.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { AppStore } from '../store.js';

/**
 * C11's prefs RPC declarations, augmenting the shared {@link RpcMethods} registry without editing
 * the frozen C1/C2 bus files (rule 4 — the seam). Keys distinct from every other slice's.
 *
 * **Bus status:** both MODELED, NOT LIVE — they land with service B13 (V3). Shapes mirror the bus
 * contract's prefs pair: a flat starred-id list, round-tripped in both directions.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Load the persisted favorite-id list. Empty params; reply carries the saved ids. */
    'tui.load_favorites': {
      params: Record<string, never>;
      result: { ok: boolean; favorites: readonly string[] };
    };
    /** Persist the favorite-id list. Echoes back the saved ids on success. */
    'tui.save_favorites': {
      params: { favorites: readonly string[] };
      result: { ok: boolean; favorites: readonly string[] };
    };
  }
}

/** The favorites actions, bound to one {@link BusClient} + store handle. */
export interface FavoritesActions {
  /**
   * Load the persisted favorites via `tui.load_favorites` (once, at startup). Ref-swaps the slice to
   * `loading`, then `ready` with the loaded id set (or `error` on rejection — never thrown past the
   * action, so the startup invalidation/boot stays fire-and-forget).
   */
  load(): Promise<void>;
  /**
   * Toggle one id's starred state, then persist via `tui.save_favorites`. Local-first: the set
   * changes immediately; the RPC is fire-and-forget persistence. The id is the panel's resolved
   * highlighted-row id (filename for docs, agentId for crows).
   */
  toggle(id: string): Promise<void>;
  /**
   * Set one id's starred state explicitly (not a toggle), then persist. Used where the desired end
   * state is known (e.g. "ensure this crow is favorited on the keep-pane-active path"). A no-op (no
   * RPC) when the id is already in the wanted state, so callers can call it idempotently.
   */
  setStarred(id: string, starred: boolean): Promise<void>;
}

/** Project a `tui.load_favorites` reply's id list into a Set, defensively (the wire may omit it). */
function toIdSet(favorites: readonly string[] | undefined): Set<string> {
  return new Set(favorites ?? []);
}

export function createFavoritesActions(
  bus: BusClient,
  store: StoreApi<AppStore>,
): FavoritesActions {
  /** Persist the given id set via `tui.save_favorites`. Shared by `toggle`/`setStarred`. */
  async function persist(ids: ReadonlySet<string>): Promise<void> {
    try {
      await bus.rpc('tui.save_favorites', { favorites: [...ids] });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      store.setState((state) => ({ favorites: { ...state.favorites, error: message } }));
    }
  }

  /** Ref-swap the local `ids` set (and clear any prior error). Returns the new set. */
  function writeIds(next: Set<string>): void {
    store.setState((state) => ({
      favorites: { ...state.favorites, ids: next, status: 'ready', error: null },
    }));
  }

  return {
    async load(): Promise<void> {
      store.setState((state) => ({ favorites: { ...state.favorites, status: 'loading' } }));
      try {
        const reply = await bus.rpc('tui.load_favorites', {});
        store.setState({
          favorites: { ids: toIdSet(reply.favorites), status: 'ready', error: null },
        });
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => ({
          favorites: { ...state.favorites, status: 'error', error: message },
        }));
      }
    },

    async toggle(id: string): Promise<void> {
      const current = store.getState().favorites.ids;
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      writeIds(next);
      await persist(next);
    },

    async setStarred(id: string, starred: boolean): Promise<void> {
      const current = store.getState().favorites.ids;
      if (current.has(id) === starred) {
        return; // already in the wanted state — no write, no RPC.
      }
      const next = new Set(current);
      if (starred) {
        next.add(id);
      } else {
        next.delete(id);
      }
      writeIds(next);
      await persist(next);
    },
  };
}
