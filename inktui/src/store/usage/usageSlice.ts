/**
 * Usage slice — domain state for the usage panel (panel 9).
 *
 * A thin shell over the shared list-slice factory (`../listSlice.js`), exactly like the roster
 * reference (`../roster/rosterSlice.js`). The `{ rows, status, error }` mechanics live in the
 * factory once; this file supplies only the row type, the slice key (`usage`), and the
 * invalidating entity. Presentation (pct formatting, bar-width, time-remaining label) stays out
 * of this file — it belongs in the selector (rule 2). The slice holds raw, wire-faithful domain
 * data only.
 *
 * Copy this file to add slice X: rename `UsageRow`→`XRow` and its fields for X's DTO, point
 * `USAGE_INVALIDATING_ENTITY` at X's {@link Entity} key, and pass X's key to `createListSlice`.
 */

import type { Entity } from '../../bus/protocol.js';
import { createListSlice, initialListState, type ListState } from '../listSlice.js';

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
  /** RT5 per-harness steering: 'auto' | 'pause' | 'prefer'. Wire-faithful, presentation-free. */
  readonly steering: string;
}

/**
 * The usage slice's state — the shared {@link ListState} shape specialized to {@link UsageRow}.
 * Selectors read `UsageState['status']`, so the union is part of the contract.
 */
export type UsageState = ListState<UsageRow>;

/**
 * The {@link Entity} key whose `state.snapshot` events invalidate this slice. Usage gauges are
 * embedded in the schedule snapshot and are driven by `harness_usage_snapshots` / scheduler
 * decision-cache writes, which the service emits under the `'queue_row'` entity (F1 locked map:
 * `queue_row → usage`). Keying here on `'queue_row'` keeps the invalidation table 1:1 with the
 * `Entity` enum (one entity → one slice), so usage refreshes on usage-visible mutations rather
 * than piggybacking on every agent change.
 */
export const USAGE_INVALIDATING_ENTITY: Entity = 'queue_row';

/** The initial, pre-fetch slice value. A fresh store has not talked to the bus yet → `idle`. */
export const initialUsageState: UsageState = initialListState<UsageRow>();

/**
 * Slice factory — the trivial Zustand `StateCreator` that seeds the `usage` key, built from the
 * shared {@link createListSlice}. Contributes only its own key; `../store.ts` composes it with
 * siblings. No bus dependency here (rule 4) — mutation is the action layer's job.
 */
export const createUsageSlice = createListSlice('usage', initialUsageState);
