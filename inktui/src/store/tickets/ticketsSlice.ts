/**
 * Tickets slice — domain state for the tickets list (panel 4).
 *
 * Copied from {@link ../roster/rosterSlice.js} per the copy recipe. Changes vs. the roster:
 *  - Row shape mirrors {@link ScheduleTicketRow} from `murder/app/service/client_api.py`
 *    (id, title, status, last_update_at, last_update_label, schedule_at, harness, model,
 *    pending_dep_ids). `plan` and `worktree` are NOT on the wire DTO (contract gap — see
 *    C7 status note in the plan); the selector renders `'—'` for those cells until B13 lands.
 *  - Invalidating entity: `'ticket'` (already in `protocol.ts` — no contract gap here).
 *  - The reply bundles active/recent_done/archived tickets; the action's `project` fn flattens all
 *    three into one list (active first). That flatten is the tickets-specific divergence — it lives
 *    in the injected projection, not in the generic factory (see ticketsActions.ts).
 *  - `pending_dep_ids` carries the non-done dep ids as a string array (the bus DTO replaces
 *    the old `deps_ok: bool` per the Bus contract's DTO note — service B5).
 *  - Runtime state (status, schedule_at, attempts) is DB-only, delivered in the row DTO, never
 *    in the ticket body the editor (C8) shows.
 *
 * Presentation (sort, truncation, deps cell, alternating-row parity) stays out of this file —
 * that is the selector's job (rule 2). The slice holds raw, wire-faithful domain data only. The
 * shared `{ rows, status, error }` mechanics come from the generic {@link ListState} +
 * {@link createListSlice} factory in `../listSlice.js`.
 *
 * Copy this file to add slice X: rename the row type + its fields for X's DTO, change
 * TICKETS_INVALIDATING_ENTITY to X's Entity key, and pass X's key to `createListSlice`.
 */

import type { Entity } from '../../bus/protocol.js';
import { createListSlice, initialListState, type ListState } from '../listSlice.js';

/**
 * One ticket as the tickets slice cares about it — a faithful, presentation-free projection of
 * the service's {@link ScheduleTicketRow} DTO. No sort key, no truncated label, no column tuple:
 * those are the selector's output (rule 2). `null` mirrors the wire's optional fields exactly.
 *
 * `pendingDepIds` carries the ids of non-done dependencies (replaces the old `deps_ok: bool` —
 * Bus contract DTO note, service B5). An empty array means all deps are done.
 *
 * CONTRACT GAP: `plan` and `worktree` are ticket *frontmatter* fields (part of the ticket body
 * in the editor), but are not present on `ScheduleTicketRow` from the service's schedule
 * snapshot. The selector renders `'—'` for those cells. This gap will be resolved when the
 * service adds these fields to the row DTO (B13 surface cleanup).
 */
export interface TicketRow {
  readonly id: string;
  readonly title: string;
  readonly status: string;
  /** ISO-8601 string for `last_update_at`; the selector formats it for display. */
  readonly lastUpdateAt: string;
  /** Human-readable label for the last update (e.g. "agent summarized"). */
  readonly lastUpdateLabel: string;
  /** Formatted schedule string, or null if unscheduled. */
  readonly scheduleAt: string | null;
  readonly harness: string | null;
  readonly model: string | null;
  /** Non-done dependency ticket ids. Empty = all deps satisfied. */
  readonly pendingDepIds: readonly string[];
  /** The id of this ticket's parent ticket, or `null` for a top-level ticket. Drives the subticket
   * tree (mirrors `PlanRow.parent`; the slice stays flat, the selector computes the tree + indent). */
  readonly parent: string | null;
}

/**
 * The tickets slice's state — the shared {@link ListState} shape specialized to {@link TicketRow}.
 * `rows` is domain data, `status` makes the load lifecycle explicit. Selectors read
 * `TicketsState['status']`, so the `'idle' | 'loading' | 'ready' | 'error'` union is part of the
 * contract.
 */
export type TicketsState = ListState<TicketRow>;

/**
 * The {@link Entity} key whose `state.snapshot` events invalidate this slice. Tickets use the
 * `'ticket'` entity key, which is already present in `protocol.ts` — no contract gap unlike
 * the `'report'` entity added in C6. The service emits `'ticket'`-keyed change events when the
 * ticket schedule changes; the store re-pulls only the tickets slice (see `../store.ts`).
 */
export const TICKETS_INVALIDATING_ENTITY: Entity = 'ticket';

/** The initial, pre-fetch slice value. A fresh store has not talked to the bus yet → `idle`. */
export const initialTicketsState: TicketsState = initialListState<TicketRow>();

/**
 * Slice factory — the trivial Zustand `StateCreator` that seeds the `tickets` key, built from the
 * shared {@link createListSlice}. Contributes only the `tickets` key; `../store.ts` composes it.
 */
export const createTicketsSlice = createListSlice('tickets', initialTicketsState);
