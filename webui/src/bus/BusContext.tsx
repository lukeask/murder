/**
 * Bus React context. The live {@link WsBusClient} is created once at the entrypoint and provided
 * here so display-only streaming consumers (the tmux terminal-frame view) can open transient
 * subscriptions. Domain DATA still flows exclusively through store actions (rule 3); this context
 * is only for streaming display data the store does not own (mirrors inktui's `useBusClient`).
 */

import { createContext, useContext } from 'react';
import type { WsBusClient } from './WsBusClient.js';

const BusContext = createContext<WsBusClient | null>(null);

export const BusProvider = BusContext.Provider;

export function useBus(): WsBusClient {
  const bus = useContext(BusContext);
  if (bus === null) {
    throw new Error('useBus must be used within a <BusProvider>');
  }
  return bus;
}
