/**
 * Pure schedule projection — the single conversion from a schedule snapshot reply to tickets and
 * usage state.
 *
 * Owns bucket flattening in the order `active`, `recent_done`, `archived`, plus both row
 * conversions. Hydration and refresh both call {@link projectScheduleSnapshot} after unwrapping
 * (`{ ok, value }` for hydrate; `asQueryResult` for refresh). The projection starts after unwrap;
 * both callers pass the same {@link ScheduleSnapshotReply} shape. Writes stay out of this module.
 */

import type { UsageRow, UsageState } from '../usage/usageSlice.js';
import type {
  ScheduleSnapshotReply,
  ScheduleUsageGaugeDto,
  TicketDto,
} from './ticketsActions.js';
import type { TicketRow, TicketsState } from './ticketsSlice.js';

/** Project one wire ticket into the slice's row. Pure: single DTO→domain mapping point. */
export function toTicketRow(dto: TicketDto): TicketRow {
  return {
    id: dto.id,
    title: dto.title,
    status: dto.status,
    lastUpdateAt: dto.last_update_at,
    lastUpdateLabel: dto.last_update_label,
    scheduleAt: dto.schedule_at ?? null,
    harness: dto.harness ?? null,
    model: dto.model ?? null,
    pendingDepIds: dto.pending_dep_ids,
    parent: dto.parent ?? null,
  };
}

/** Project one wire gauge into the slice's row. Pure: single DTO→domain mapping point. */
export function toUsageRow(dto: ScheduleUsageGaugeDto): UsageRow {
  return {
    harness: dto.harness,
    windowKey: dto.window_key,
    pct: dto.pct,
    tUntilResetMinutes: dto.t_until_reset_minutes,
    tPeriodMinutes: dto.t_period_minutes ?? 0,
    steering: dto.steering ?? 'auto',
    fetchedAt: dto.fetched_at ?? null,
  };
}

/**
 * Flatten the three ticket buckets into one row list (active, recent_done, archived), project
 * usage gauges, and return both slices as `ready` state. Callers that need a loading lifecycle
 * apply that themselves before the shared refresh drain.
 */
export function projectScheduleSnapshot(
  reply: ScheduleSnapshotReply,
): { tickets: TicketsState; usage: UsageState } {
  const ticketRows = [
    ...reply.active_tickets,
    ...reply.recent_done_tickets,
    ...reply.archived_tickets,
  ].map(toTicketRow);
  return {
    tickets: { rows: ticketRows, status: 'ready', error: null },
    usage: {
      rows: reply.usage_gauges.map(toUsageRow),
      status: 'ready',
      error: null,
    },
  };
}
