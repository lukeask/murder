/**
 * Tickets actions — the *only* code that calls the bus for ticket data (rule 3).
 *
 * Copied from {@link ../roster/rosterActions.js} per the C3 copy recipe. Changes vs. the roster:
 *  - RPC is `ticket.get_snapshot` (modeled per bus-contract naming `domain.verb`, mirrors Python
 *    `RuntimeClient.get_schedule_snapshot()`). NOT yet on the live bus — flag not-live; confirm
 *    name/shape when service B13 lands.
 *  - Reply shape is {@link TicketSnapshotReply} (mirrors Python `ScheduleSnapshot`). The reply
 *    bundles active_tickets / recent_done_tickets / archived_tickets; the action flattens all
 *    three into one slice row list (consistent with the Textual `sorted_rows` combining them).
 *  - Projection is `toTicketRow` (wire DTO → presentation-free {@link TicketRow}).
 *  - `pending_dep_ids` replaces the old `deps_ok: bool` — the non-done dep ids (service B5).
 *  - Ref-swaps `state.tickets`, not `state.roster`.
 *  - `declare module` augments `RpcMethods` with `'ticket.get_snapshot'` — distinct from every
 *    other slice's key; never redeclare an existing one.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { AppStore } from '../store.js';
import type { TicketRow, TicketsState } from './ticketsSlice.js';

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
   * combined into one flat list (active first, matching the Textual `sorted_rows` convention).
   * The selector applies the final display sort (last_update_at desc — rule 2).
   */
  refresh(): Promise<void>;
}

export function createTicketsActions(bus: BusClient, store: StoreApi<AppStore>): TicketsActions {
  return {
    async refresh(): Promise<void> {
      // Ref-swap ONLY the tickets slice — sibling slices keep identity (invalidation-granularity
      // contract). Mirrors the roster action's loading→ready/error lifecycle exactly.
      store.setState((state) => ({ tickets: { ...state.tickets, status: 'loading' } }));
      try {
        const reply = await bus.rpc('ticket.get_snapshot', {});
        // Flatten all three buckets; active first (natural display priority), then done, archived.
        const allDtos = [
          ...reply.active_tickets,
          ...reply.recent_done_tickets,
          ...reply.archived_tickets,
        ];
        const rows = allDtos.map(toTicketRow);
        const next: TicketsState = { rows, status: 'ready', error: null };
        store.setState({ tickets: next });
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => ({
          tickets: { ...state.tickets, status: 'error', error: message },
        }));
      }
    },
  };
}
