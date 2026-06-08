/**
 * Roster slice — the reference domain slice for the whole store layer.
 *
 * A slice is one domain's state plus the actions that mutate it. This file owns the *state shape*
 * and the slice factory; the bus-calling work lives in {@link ./rosterActions.js} so rule 3 (actions
 * are the only view→bus path) is enforced by file boundary, not by convention. Presentation
 * (sort/truncate/columns) is deliberately absent — that is the selector's job (rule 2). What lands
 * here is domain data only, exactly as the service delivers it, so the slice stays reusable by a
 * future React-DOM client (rule 4).
 *
 * Copy this file to add slice X: rename `RosterState`→`XState`/`RosterRow`→`XRow`, swap the row
 * fields for X's DTO, point `INVALIDATING_ENTITY` at X's {@link Entity} key, and rename the slice
 * key. The composition wiring in `../store.ts` and the action in `./rosterActions.ts` follow the
 * same copy recipe — three small files, no framework glue to re-derive.
 */

import type { StateCreator } from 'zustand';
import type { Entity } from '../../bus/protocol.js';
import type { AppStore } from '../store.js';

/**
 * One crow as the roster cares about it — a faithful, presentation-free projection of the service's
 * crow-session DTO (Python `CrowSessionSummary`). No sort key, no truncated label, no column tuple:
 * those are the selector's output, never the store's (rule 2). `null` mirrors the wire's optional
 * fields so a missing value is explicit, never an empty-string sentinel.
 */
export interface RosterRow {
  readonly agentId: string;
  readonly ticketId: string | null;
  readonly ticketTitle: string | null;
  readonly harness: string | null;
  readonly model: string | null;
  readonly status: string;
  readonly session: string | null;
}

/**
 * The roster slice's state. `rows` is the domain data; `status` makes the load lifecycle explicit so
 * a component can distinguish "not fetched yet" from "fetched, empty" without a sentinel. Every
 * field is readonly: the slice is ref-swapped wholesale on change (the invalidation-granularity
 * contract), never mutated in place.
 */
export interface RosterState {
  readonly rows: readonly RosterRow[];
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last refresh rejected; cleared on the next successful load. */
  readonly error: string | null;
}

/**
 * The {@link Entity} key whose `state.snapshot` events invalidate this slice. The service emits
 * key-only change events naming the entity that changed; the store re-pulls *only* the slice whose
 * `INVALIDATING_ENTITY` matches (see `../store.ts`). Crows are `agent`-keyed on the wire.
 */
export const ROSTER_INVALIDATING_ENTITY: Entity = 'agent';

/** The initial, pre-fetch slice value. A fresh store has not talked to the bus yet → `idle`. */
export const initialRosterState: RosterState = {
  rows: [],
  status: 'idle',
  error: null,
};

/**
 * Slice factory in Zustand's standard `StateCreator` shape, scoped to the combined {@link AppStore}
 * via the `[]` middleware tuple + the slice's own state as the fourth type arg. It contributes only
 * its own keys; `../store.ts` composes it with sibling slices into the one root store. The slice
 * holds state, not actions: mutation is done by the action layer calling `set` through the store
 * handle, keeping the bus dependency out of this framework-/transport-agnostic file (rule 4).
 */
export const createRosterSlice: StateCreator<AppStore, [], [], { roster: RosterState }> = () => ({
  roster: initialRosterState,
});
