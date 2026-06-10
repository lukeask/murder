/**
 * React binding for the {@link BusClient} — the component layer's injection point for the bus
 * *subscription* side (rule 4: the client is injected, never imported by a component).
 *
 * This context is narrow by design: **actions are still the only view→bus RPC path** (rule 3).
 * The `useBusClient` hook is exposed solely so a component that manages a transient streaming
 * subscription (C14's `TmuxFrame`) can open and close it without threading the bus through a
 * domain action. Streaming ANSI frames are transient display data, not a domain slice; they have
 * no invalidation entry in `store.ts` and no action in any slice. The subscription is opened
 * inside a `useEffect` in the consuming component, so cleanup is tied to component unmount —
 * the only exit-path-agnostic lifecycle anchor (every mode exit, whether via `ctrl+y` or the
 * dismiss key, unmounts the component, which triggers the cleanup).
 *
 * Any future component that needs a transient subscription (not a domain-slice refresh) should
 * use this hook and manage cleanup in `useEffect`. Domain state still goes through actions (rule 3).
 *
 * The {@link App} root mounts the {@link BusClientProvider} alongside the store provider; tests
 * supply a {@link FakeBusClient} the same way tests supply a fake store.
 */

import { createContext, useContext } from 'react';
import type { BusClient } from '../bus/BusClient.js';

/** Carries the one bus-client instance to the component tree. `null` outside a provider so the
 * hook fails loudly on a wiring bug (mirrors `AppStoreContext`). */
export const BusClientContext = createContext<BusClient | null>(null);

/** The provider component — re-exported so the app root imports one name. */
export const BusClientProvider = BusClientContext.Provider;

/**
 * Read the injected {@link BusClient}. Throws if used outside a {@link BusClientProvider}.
 * Only use this hook for transient streaming subscriptions managed in `useEffect`; domain
 * data reads go through `useAppStore` and actions (rule 3).
 */
export function useBusClient(): BusClient {
  const bus = useContext(BusClientContext);
  if (bus === null) {
    throw new Error('useBusClient must be used within a <BusClientProvider>.');
  }
  return bus;
}
