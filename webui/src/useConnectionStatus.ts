/**
 * `useConnectionStatus` — exposes the {@link WsBusClient}'s live connection state to React.
 *
 * The status hooks (`onConnect`/`onDisconnect`/`onPermanentError`) are off the BusClient interface
 * — the wiring narrows for them structurally, exactly as the Ink app does (see UdsBusClient). This
 * hook subscribes to those callbacks and reflects them as a small union the header renders.
 */

import { useEffect, useState } from 'react';
import type { WsBusClient } from './bus/WsBusClient.js';

export type ConnectionStatus = 'connecting' | 'connected' | 'reconnecting' | 'error';

export function useConnectionStatus(bus: WsBusClient): ConnectionStatus {
  const [status, setStatus] = useState<ConnectionStatus>('connecting');
  useEffect(() => {
    const offConnect = bus.onConnect(() => setStatus('connected'));
    const offDisconnect = bus.onDisconnect(() => setStatus('reconnecting'));
    const offError = bus.onPermanentError(() => setStatus('error'));
    return () => {
      offConnect();
      offDisconnect();
      offError();
    };
  }, [bus]);
  return status;
}
