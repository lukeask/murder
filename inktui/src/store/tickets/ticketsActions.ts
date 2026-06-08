/**
 * Tickets actions — the *only* code that calls the bus for ticket data (rule 3).
 *
 * Copied from {@link ../roster/rosterActions.js} per the copy recipe. Changes vs. the roster:
 *  - RPC is `ticket.get_snapshot` (modeled per bus-contract naming `domain.verb`, mirrors Python
 *    `RuntimeClient.get_schedule_snapshot()`). NOT yet on the live bus — flag not-live; confirm
 *    name/shape when service B13 lands.
 *  - Reply shape is {@link TicketSnapshotReply} (mirrors Python `ScheduleSnapshot`). The reply
 *    bundles active_tickets / recent_done_tickets / archived_tickets.
 *  - **Tickets-specific divergence:** the `project` fn FLATTENS all three buckets into one row list
 *    (active first, then recent_done, then archived — matching the Textual `sorted_rows`
 *    convention) before mapping with `toTicketRow`. This 3-bucket flatten is exactly why `project`
 *    is the per-domain injection point: the generic {@link createRefreshAction} never special-cases
 *    tickets — it just calls whatever `project` it's given. Roster/notes/reports pass a one-liner
 *    `.map`; tickets passes a flatten-then-map. The divergence is data, not a branch in the factory.
 *  - Passes the `tickets` slice key to `createRefreshAction`.
 *  - `pending_dep_ids` replaces the old `deps_ok: bool` — the non-done dep ids (service B5).
 *  - `declare module` augments `RpcMethods` with `'ticket.get_snapshot'` — distinct from every
 *    other slice's key; never redeclare an existing one.
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
 * Declares the ticket-schedule read RPC via declaration merging. `ticket.get_snapshot` is the
 * bus-contract name (`domain.verb`, mirrors Python `get_schedule_snapshot()`). NOT yet on the
 * live bus — modeled per the contract's "view → service = RPC methods" rule; confirm name/shape
 * when service B13 lands. Augments `RpcMethods` with its OWN key (never redeclares an existing).
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Fetch the full ticket schedule snapshot. Re-pulled on each `ticket`-entity `state.snapshot`. */
    'ticket.get_snapshot': { params: Record<string, never>; result: TicketSnapshotReply };
  }
}

/**
 * The `ticket.get_snapshot` reply, mirroring the service's `ScheduleSnapshot` DTO from
 * `murder/app/service/client_api.py`. Only the fields the tickets slice projects are typed;
 * the wire may carry more (e.g. scheduler_decisions, usage_gauges — those are C9's domain).
 */
export interface TicketSnapshotReply {
  active_tickets: readonly TicketDto[];
  recent_done_tickets: readonly TicketDto[];
  archived_tickets: readonly TicketDto[];
  invalidation_key: string;
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
  };
}

/**
 * Flatten the three ticket buckets into one row list, then project. Active first (natural display
 * priority), then recent_done, then archived. This is the tickets-only divergence injected into the
 * shared {@link createRefreshAction} via its `project` parameter — the generic stays domain-blind.
 * The selector applies the final display sort (last_update_at desc — rule 2).
 */
function projectTickets(reply: TicketSnapshotReply): readonly TicketRow[] {
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
    method: 'ticket.get_snapshot',
    project: projectTickets,
  });
}
