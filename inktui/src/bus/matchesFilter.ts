/**
 * Server-side {@link EventFilter} semantics, mirrored client-side for fanout on the multiplexed
 * connection (Python `EventFilter.matches`): fields compose with AND; an absent filter field
 * matches any.
 *
 * Single source of truth shared by both bus implementations: {@link UdsBusClient} fans out pushed
 * `pub` frames through it, and {@link FakeBusClient} fans out emitted events through it, so the two
 * impls cannot drift. Exported so a test can reason about which events a filtered subscription
 * should receive.
 */

import type { BusEvent, EventFilter } from './protocol.js';

export function matchesFilter(event: BusEvent, filter: EventFilter | undefined): boolean {
  if (filter === undefined) {
    return true;
  }
  return (
    fieldMatches(filter.role, getField(event, 'role')) &&
    fieldMatches(filter.ticket_id, getField(event, 'ticket_id')) &&
    fieldMatches(filter.type, event.type) &&
    fieldMatches(filter.entity, getField(event, 'entity')) &&
    fieldMatches(filter.target_worker, getField(event, 'target_worker')) &&
    fieldMatches(filter.kind, getField(event, 'kind'))
  );
}

/** A filter field matches when it is absent, or equal to the event's value for that field. */
function fieldMatches<T>(expected: T | undefined, actual: unknown): boolean {
  return expected === undefined || expected === actual;
}

/** Reads an optional field present on only some event kinds, without `any`. Returns `undefined`
 * when the event kind lacks the field (so a filter on it can't match a partial). */
function getField(event: BusEvent, field: string): unknown {
  return (event as unknown as Record<string, unknown>)[field];
}
