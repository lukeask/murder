/**
 * React binding for the {@link AppStore} — the component layer's only door to the store.
 *
 * The store itself ({@link createAppStore}) is framework-agnostic vanilla Zustand (rule 4). This
 * file is the thin React adapter: a context that carries the one store instance (constructed with
 * the injected `BusClient` at the app root, so the bus dependency is wired once and never imported by
 * a component), and a `useAppStore(selector, equality?)` hook that subscribes a component to exactly
 * the slice its selector picks. Pass `shallow` as the equality fn for object/array selections so a
 * ref-swap of an *unrelated* slice does not re-render this component — the rule-1 over-render guard,
 * not hand-rolled (`useStore` + `shallow` give referential stability per selector for free).
 *
 * A component thus reads `const roster = useAppStore((s) => s.roster, shallow);` and dispatches
 * `const refresh = useAppStore((s) => s.actions.roster.refresh);` — never touching the bus.
 */

import { createContext, useContext } from 'react';
import { useStoreWithEqualityFn } from 'zustand/traditional';
import type { AppStore, AppStoreApi } from '../store/store.js';

/** Carries the single store instance to the component tree. `null` outside a provider so the hook
 * can fail loudly (a missing `<AppStoreProvider>` is a wiring bug, not a silent empty store). */
export const AppStoreContext = createContext<AppStoreApi | null>(null);

/** The provider component supplies this — re-exported so the app root imports one name. */
export const AppStoreProvider = AppStoreContext.Provider;

/**
 * Subscribe to a selected view of the store. `selector` narrows the state to what this component
 * needs; `equality` (pass `shallow` for object/array results) suppresses re-renders when the
 * selection is value-equal to the last. Throws if used outside an {@link AppStoreProvider}.
 */
export function useAppStore<T>(
  selector: (state: AppStore) => T,
  equality?: (a: T, b: T) => boolean,
): T {
  const store = useContext(AppStoreContext);
  if (store === null) {
    throw new Error('useAppStore must be used within an <AppStoreProvider>.');
  }
  return useStoreWithEqualityFn(store, selector, equality);
}

/**
 * Return the raw {@link AppStoreApi} handle (not a subscribed selection). For the rare consumer that
 * needs imperative `getState()`/`setState` access outside React's render-subscribe cycle — e.g. a
 * C7M *mode* factory, which is plain data (not a component) and reads slice state inside its
 * `onIntent`/`onUncaptured` handlers. Components that render off the store use {@link useAppStore}
 * instead; reach for this only when there is no component to subscribe (rule 1 still holds — the
 * mode dispatches actions, never the bus). Throws if used outside an {@link AppStoreProvider}.
 */
export function useAppStoreApi(): AppStoreApi {
  const store = useContext(AppStoreContext);
  if (store === null) {
    throw new Error('useAppStoreApi must be used within an <AppStoreProvider>.');
  }
  return store;
}
