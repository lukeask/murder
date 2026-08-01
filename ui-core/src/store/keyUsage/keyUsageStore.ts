/**
 * `keyUsageStore` — recency-decayed keybinding usage counts for surfacing frequently-used actions.
 *
 * Each dispatched binding records an `action` id (a global {@link ActionId}, a `mode:<intent>`, or a
 * `<panelId>:<intent>` panel chord) via {@link KeyUsageState.recordUse}. Counts decay exponentially
 * with a 3-day half-life so stale habits fade without a hard reset. Persistence is wired in a later
 * slice; this module is the in-memory truth only.
 *
 * ## Two exports, two consumers (mirrors {@link ../toast/toastStore.js toastStore})
 *
 *  - {@link createKeyUsageStore} — the factory for isolated unit tests.
 *  - {@link keyUsageStore} — the app-level singleton production callers import.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';

/** Half-life for recency decay: after this many ms since {@link KeyUsageRecord.lastAt}, the stored
 * count contributes half its face value to the next {@link decayedCount}. */
export const KEY_USAGE_HALF_LIFE_MS = 3 * 24 * 60 * 60 * 1000;

/** One action's usage snapshot — plain data, no methods. */
export interface KeyUsageRecord {
  /** Recency-decayed use count (see {@link decayedCount}). */
  readonly count: number;
  /** Epoch ms of the most recent {@link KeyUsageState.recordUse}. */
  readonly lastAt: number;
}

/** Pure decay: how much of `record.count` still counts at instant `now`. */
export function decayedCount(record: KeyUsageRecord, now: number): number {
  return record.count * 0.5 ** ((now - record.lastAt) / KEY_USAGE_HALF_LIFE_MS);
}

/** The key-usage store's state: the per-action records plus the verbs. */
export interface KeyUsageState {
  /** Per-action usage records keyed by the dispatcher's `action` id string. */
  readonly actions: Readonly<Record<string, KeyUsageRecord>>;
  /**
   * Record one use of `actionId` at `now` (defaults to `Date.now()`). New count =
   * `decayedCount(existing, now) + 1`, or `1` when absent; `lastAt` is set to `now`.
   */
  recordUse(actionId: string, now?: number): void;
  /** Replace state wholesale — for loading persisted data later. */
  hydrate(actions: Record<string, KeyUsageRecord>): void;
}

export type KeyUsageStoreApi = StoreApi<KeyUsageState>;

/** Create a key-usage store. Each call is an independent instance — unit tests use this. */
export function createKeyUsageStore(): KeyUsageStoreApi {
  return createStore<KeyUsageState>()((set, get) => ({
    actions: {},
    recordUse(actionId, now = Date.now()) {
      const existing = get().actions[actionId];
      const count = existing === undefined ? 1 : decayedCount(existing, now) + 1;
      set((state) => ({
        actions: { ...state.actions, [actionId]: { count, lastAt: now } },
      }));
    },
    hydrate(actions) {
      set({ actions });
    },
  }));
}

/** The app-level singleton key-usage store — the instance production code imports. */
export const keyUsageStore: KeyUsageStoreApi = createKeyUsageStore();
