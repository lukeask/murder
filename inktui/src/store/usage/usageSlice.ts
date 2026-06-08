/**
 * Usage slice — domain state for the usage panel (panel 9).
 *
 * Copied from {@link ../roster/rosterSlice.js} per the C3 copy recipe. Changes vs. the roster:
 *  - Row shape mirrors {@link UsageGaugeSummary} from `murder/app/service/client_api.py`
 *    (harness, window_key, pct, t_until_reset_minutes, t_period_minutes).
 *  - Invalidating entity: `'agent'` — the same entity as the roster. Usage changes when agents
 *    run, and there is no dedicated `'usage'` entity key in the Python `Entity` enum. Multiple
 *    slices keying the same entity is supported: the invalidation loop fires all matches.
 *  - Presentation (pct formatting, bar-width, time-remaining label) stays out of this file —
 *    it belongs in the selector (rule 2). The slice holds raw, wire-faithful domain data only.
 *
 * Copy this file to add slice X: rename state/row types, swap row fields for X's DTO,
 * change USAGE_INVALIDATING_ENTITY to X's Entity key, rename the slice key + initial const.
 */

import type { StateCreator } from 'zustand';
import type { Entity } from '../../bus/protocol.js';
import type { AppStore } from '../store.js';

/**
 * One usage gauge as the usage slice cares about it — a faithful, presentation-free projection
 * of the service's `UsageGaugeSummary` DTO. No formatted label, no bar string, no truncation:
 * those are the selector's output (rule 2).
 */
export interface UsageRow {
  readonly harness: string;
  readonly windowKey: string;
  /** Usage percentage 0–100+. Raw float; the selector formats it. */
  readonly pct: number;
  /** Minutes until the rate-limit window resets. The selector formats as "Xm". */
  readonly tUntilResetMinutes: number;
  /** Total window length in minutes. 0 when unknown. */
  readonly tPeriodMinutes: number;
}

/**
 * The usage slice's state. Same shape as {@link RosterState}: `rows` is domain data, `status`
 * makes the load lifecycle explicit so a component can distinguish "not fetched yet" from
 * "fetched, empty". Every field is readonly — ref-swapped wholesale on change.
 */
export interface UsageState {
  readonly rows: readonly UsageRow[];
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last refresh rejected; cleared on the next successful load. */
  readonly error: string | null;
}

/**
 * The {@link Entity} key whose `state.snapshot` events invalidate this slice. Usage changes as
 * agents run, so it keys on `'agent'` — the same entity as the roster slice. There is no
 * dedicated `'usage'` entity in the Python `Entity` enum; using `'agent'` is the closest fit
 * and correct for refresh granularity. The invalidation loop fires all matching slices on the
 * same entity, so both roster and usage refresh when an `'agent'`-entity event arrives.
 */
export const USAGE_INVALIDATING_ENTITY: Entity = 'agent';

/** The initial, pre-fetch slice value. A fresh store has not talked to the bus yet → `idle`. */
export const initialUsageState: UsageState = {
  rows: [],
  status: 'idle',
  error: null,
};

/**
 * Slice factory in Zustand's standard `StateCreator` shape, scoped to the combined
 * {@link AppStore}. Contributes only the `usage` key; `../store.ts` composes it with siblings.
 */
export const createUsageSlice: StateCreator<AppStore, [], [], { usage: UsageState }> = () => ({
  usage: initialUsageState,
});
