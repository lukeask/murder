/**
 * Tickets actions — the *only* code that calls the bus for ticket data (rule 3).
 *
 * Copied from {@link ../roster/rosterActions.js} per the copy recipe. Changes vs. the roster:
 *  - Query is the live `schedule.get` application-protocol capability.
 *  - Reply shape is {@link ScheduleSnapshotReply} (mirrors Python `ScheduleSnapshot`). The reply
 *    bundles active_tickets / recent_done_tickets / archived_tickets AND `usage_gauges` (the usage
 *    slice consumes the same reply — usage is embedded in the schedule snapshot, not its own RPC).
 *  - **Tickets-specific divergence:** the `project` fn FLATTENS all three buckets into one row list
 *    (active first, then recent_done, then archived — matching the Textual `sorted_rows`
 *    convention) before mapping with `toTicketRow`. This 3-bucket flatten is exactly why `project`
 *    is the per-domain injection point: the generic {@link createRefreshAction} never special-cases
 *    tickets — it just calls whatever `project` it's given. Roster/notes/reports pass a one-liner
 *    `.map`; tickets passes a flatten-then-map. The divergence is data, not a branch in the factory.
 *  - Passes the `tickets` slice key to `createRefreshAction`.
 *  - `pending_dep_ids` replaces the old `deps_ok: bool` — the non-done dep ids (service B5).
 *  - `declare module` augments `RpcMethods` with `'state.schedule_snapshot'`; this module is the
 *    SOLE declaration of that key (the usage slice consumes the reply type without re-declaring —
 *    a second augmentation with a different `result` would be a TS 2717 collision).
 *
 * The loading→ready/error + ref-swap-only-this-key mechanics come from the shared
 * {@link createRefreshAction} factory in `../listSlice.js`.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import { createRefreshAction } from '../listSlice.js';
import type { AppStore } from '../store.js';
import type { TicketRow } from './ticketsSlice.js';

/**
 * Declares the schedule read RPC via declaration merging. `state.schedule_snapshot` is the LIVE
 * server name (registered in `murder/app/service/host.py`; mirrors Python `get_schedule_snapshot()`).
 */


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

/** Project one wire ticket into the slice's row. Pure: the single place the DTO→domain mapping
 * lives. No formatting — that is the selector's job (rule 2). */
function toTicketRow(dto: TicketDto): TicketRow {
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

/**
 * Flatten the three ticket buckets into one row list, then project. Active first (natural display
 * priority), then recent_done, then archived. This is the tickets-only divergence injected into the
 * shared {@link createRefreshAction} via its `project` parameter — the generic stays domain-blind.
 * The selector applies the final display sort (last_update_at desc — rule 2).
 */
function projectTickets(reply: ScheduleSnapshotReply): readonly TicketRow[] {
  return [...reply.active_tickets, ...reply.recent_done_tickets, ...reply.archived_tickets].map(
    toTicketRow,
  );
}

/**
 * The tickets actions, bound to one `BusClient` + store handle. Returned to `../store.ts`,
 * which hangs them off the store so components dispatch `store.getState().actions.tickets.refresh()`.
 */
export interface TicketsActions {
  /**
   * Re-pull the ticket schedule and ref-swap *only* the `tickets` slice. The sole bus caller for
   * ticket data. Idempotent; concurrent calls are last-write (latest reply wins). Rejections land
   * in `tickets.error` — never thrown past the action (the invalidation loop stays fire-and-forget).
   *
   * The reply bundles active + recent_done + archived tickets; all three are projected and
   * combined into one flat list (active first, matching the Textual `sorted_rows` convention) by
   * the injected `projectTickets` fn. The selector applies the final display sort (rule 2).
   */
  refresh(): Promise<void>;
}

export function createTicketsActions(bus: BusClient, store: StoreApi<AppStore>): TicketsActions {
  return createRefreshAction(bus, store, {
    key: 'tickets',
    method: 'schedule.get',
    project: projectTickets,
  });
}
