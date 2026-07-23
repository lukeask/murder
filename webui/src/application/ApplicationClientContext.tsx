/**
 * Application-client React context. The live {@link ApplicationWebSocketClient} is created once at the entrypoint and provided
 * here so display-only streaming consumers (the tmux terminal-frame view) can open transient
 * subscriptions. Domain DATA still flows exclusively through store actions (rule 3); this context
 * is only for streaming display data the store does not own (mirrors inktui's `useApplicationClientClient`).
 */

import { createContext, useContext } from 'react';
import type { ApplicationWebSocketClient } from './ApplicationWebSocketClient.js';

const ApplicationClientContext = createContext<ApplicationWebSocketClient | null>(null);

export const ApplicationClientProvider = ApplicationClientContext.Provider;

export function useApplicationClient(): ApplicationWebSocketClient {
  const bus = useContext(ApplicationClientContext);
  if (bus === null) {
    throw new Error('useApplicationClient must be used within a <ApplicationClientProvider>');
  }
  return bus;
}
