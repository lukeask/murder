/**
 * `capsStore` — the terminal-capability fact: does this terminal support the kitty keyboard protocol?
 *
 * This is the single source of truth for "can we deliver ctrl chords", written once by detection at
 * startup. Two consumers downstream:
 *  - the {@link ../input/bindingsStore.js bindingsStore}'s `ctrlAvailable` (so `ctrl`/`both` degrade
 *    to alt when unsupported — see {@link ../input/bindings.js resolveBindings}); and
 *  - a later phase's settings notice ("ctrl requires the kitty protocol — not supported by this
 *    terminal").
 *
 * Framework-agnostic vanilla Zustand (no React here), mirroring {@link ../input/bindingsStore.js}: a
 * React hook can wrap it later, but the detection wiring in `index.tsx` and any test drive it
 * directly. It starts in `'detecting'` and resolves to a boolean once the driver's probe settles.
 */

import { createStore, type StoreApi } from 'zustand/vanilla';

/** Whether the kitty keyboard protocol is supported. `'detecting'` is the transient startup state
 * before the probe settles; it never returns to `'detecting'` once resolved. */
export type KittySupport = boolean | 'detecting';

/** The capability store's state + its single setter. */
export interface CapsState {
  /** Kitty protocol support, or `'detecting'` until the startup probe resolves. */
  readonly kittySupported: KittySupport;
  /** Record the detection result (or reset to `'detecting'`). */
  setKittySupported(supported: KittySupport): void;
}

/** The handle type, re-exported so callers don't import `zustand/vanilla`. */
export type CapsStoreApi = StoreApi<CapsState>;

/** Create a capability store. Starts in `'detecting'` (or a seeded value, for tests). */
export function createCapsStore(initial: KittySupport = 'detecting'): CapsStoreApi {
  return createStore<CapsState>()((set) => ({
    kittySupported: initial,
    setKittySupported(supported) {
      set({ kittySupported: supported });
    },
  }));
}
