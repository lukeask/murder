/**
 * React binding for the {@link BusClient}. Components use it only for transient application
 * streams whose lifecycle is tied to a mounted surface.
 *
 * Domain queries and commands remain in injected action factories. The exception is terminal
 * attachment: replace-frame terminal data is an independent, non-durable stream rather than a
 * projection slice. A transcript component attaches in `useEffect`; cleanup sends a real
 * `terminal.detach` through the client when the mode exits or component unmounts.
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
 * Only use this hook for transient terminal streams managed in `useEffect`; domain
 * data reads go through `useAppStore` and actions (rule 3).
 */
export function useBusClient(): BusClient {
  const bus = useContext(BusClientContext);
  if (bus === null) {
    throw new Error('useBusClient must be used within a <BusClientProvider>.');
  }
  return bus;
}
