/**
 * Narrow an opaque application subscription payload to the compatibility event DTO union.
 *
 * The service gateway sources projection invalidations from the transitional fact tail, whose
 * payloads retain the existing `type` discriminant. Keeping this check at the transport boundary
 * avoids leaking arbitrary `Record<string, unknown>` objects into store listeners.
 */

import type { BusEvent } from './protocol.js';

export function isBusEvent(payload: unknown): payload is BusEvent {
  return (
    typeof payload === 'object' &&
    payload !== null &&
    !Array.isArray(payload) &&
    typeof (payload as Record<string, unknown>)['type'] === 'string'
  );
}
