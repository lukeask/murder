/**
 * React binding for the {@link ApplicationClient}. Components use it only for transient application
 * streams whose lifecycle is tied to a mounted surface.
 *
 * Domain queries and commands remain in injected action factories. The exception is terminal
 * attachment: replace-frame terminal data is an independent, non-durable stream rather than a
 * projection slice. A transcript component attaches in `useEffect`; cleanup sends a real
 * `terminal.detach` through the client when the mode exits or component unmounts.
 *
 * The {@link App} root mounts the {@link ApplicationClientProvider} alongside the store provider; tests
 * supply a {@link FakeApplicationClient} the same way tests supply a fake store.
 */

import { createContext, useContext } from 'react';
import type { ApplicationClient } from '../application/ApplicationClient.js';

/** Carries the one application-client instance to the component tree. `null` outside a provider so the
 * hook fails loudly on a wiring bug (mirrors `AppStoreContext`). */
export const ApplicationClientContext = createContext<ApplicationClient | null>(null);

/** The provider component — re-exported so the app root imports one name. */
export const ApplicationClientProvider = ApplicationClientContext.Provider;

/**
 * Read the injected {@link ApplicationClient}. Throws if used outside a {@link ApplicationClientProvider}.
 * Only use this hook for transient terminal streams managed in `useEffect`; domain
 * data reads go through `useAppStore` and actions (rule 3).
 */
export function useApplicationClient(): ApplicationClient {
  const client = useContext(ApplicationClientContext);
  if (client === null) {
    throw new Error('useApplicationClient must be used within a <ApplicationClientProvider>.');
  }
  return client;
}
