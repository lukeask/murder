/**
 * Tickets actions — the *only* code that calls the bus for ticket data (rule 3), via the shared
 * schedule refresh.
 *
 * Tickets and usage share one `schedule.get` query. {@link refresh} is an alias of that shared
 * refresh — it does not own separate request state. Projection lives in {@link projectScheduleSnapshot}.
 */

import type { StoreApi } from 'zustand';
import type { ApplicationClient } from '../../application/ApplicationClient.js';
import type { RefreshOptions } from '../listSlice.js';
import type { AppStore } from '../store.js';
import { createScheduleRefresh, type ScheduleRefresh } from './scheduleRefresh.js';

/**
 * The `state.schedule_snapshot` reply, mirroring the live service's `ScheduleSnapshot` DTO from
 * `murder/app/protocol/read_models.py`. Only the fields the tickets + usage slices project are typed;
 * the wire may carry more (e.g. scheduler_decisions, calendar fields). Tickets read the 3 buckets;
 * the usage slice reads `usage_gauges`.
 */
export interface ScheduleSnapshotReply {
  active_tickets: readonly TicketDto[];
  recent_done_tickets: readonly TicketDto[];
  archived_tickets: readonly TicketDto[];
  /** Usage gauges, embedded in the schedule snapshot (live `ScheduleSnapshot.usage_gauges`). */
  usage_gauges: readonly ScheduleUsageGaugeDto[];
  invalidation_key: string;
}

/**
 * One usage gauge as it crosses the wire (Python `UsageGaugeSummary`), embedded in the schedule
 * snapshot. The usage slice's action projects these; declared here because this module owns the
 * `ScheduleSnapshotReply` shape. Presentation-free — formatting is the selector's job (rule 2).
 */
export interface ScheduleUsageGaugeDto {
  harness: string;
  window_key: string;
  pct: number;
  t_until_reset_minutes: number;
  t_period_minutes?: number;
  /** RT5 per-harness steering: 'auto' | 'pause' | 'prefer' (defaults 'auto' if absent). */
  steering?: string;
  /** ISO-8601 UTC timestamp of the latest usage snapshot for this harness. */
  fetched_at?: string | null;
}

/**
 * One ticket row as it crosses the wire (Python `ScheduleTicketRow`). Presentation-free.
 * `pending_dep_ids` carries the ids of non-done deps (replaces `deps_ok: bool` — service B5).
 *
 * CONTRACT GAP: `plan` and `worktree` are NOT on the wire DTO (they're ticket frontmatter, not
 * schedule row fields). The selector renders `'—'` for those cells until the service adds them.
 */
export interface TicketDto {
  id: string;
  title: string;
  status: string;
  last_update_at: string;
  last_update_label: string;
  schedule_at?: string | null;
  harness?: string | null;
  model?: string | null;
  pending_dep_ids: readonly string[];
  /** The parent ticket's id (tickets.parent_ticket_id column). Absent or null for a top-level ticket. */
  parent?: string | null;
}

/**
 * The tickets actions, bound to one `ApplicationClient` + store handle. Returned to `../store.ts`,
 * which hangs them off the store so components dispatch `store.getState().actions.tickets.refresh()`.
 */
export interface TicketsActions {
  /**
   * Re-pull the schedule snapshot and update tickets (and usage) via the shared schedule refresh.
   * Alias of the shared refresh — no separate request state.
   */
  refresh(options?: RefreshOptions): Promise<void>;
}

export function createTicketsActions(
  bus: ApplicationClient,
  store: StoreApi<AppStore>,
  scheduleRefresh?: ScheduleRefresh,
): TicketsActions {
  const shared = scheduleRefresh ?? createScheduleRefresh(bus, store);
  return {
    refresh: (options) => shared.refresh(options),
  };
}
