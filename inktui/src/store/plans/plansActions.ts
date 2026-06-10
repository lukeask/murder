/**
 * Plans actions — the *only* code that calls the bus for plans data (rule 3).
 *
 * Copied from {@link ../notes/notesActions.js} per the copy recipe. Changes vs. notes:
 *  - RPC is `state.plans_snapshot` (registered in `host.py`; live on the bus).
 *  - Reply/DTO carry the extra `parent`, `updated_at`, and `char_count` fields from the backend.
 *  - Projection is `toPlanRow` (adds `parent`, normalising an absent value to `null`).
 *  - Passes the `plans` slice key to `createRefreshAction`.
 *  - `declare module` augments `RpcMethods` with `'state.plans_snapshot'` (its own distinct key).
 *
 * The loading→ready/error + ref-swap-only-this-key mechanics come from the shared
 * {@link createRefreshAction} factory in `../listSlice.js`.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import { createRefreshAction } from '../listSlice.js';
import type { AppStore } from '../store.js';
import type { PlanRow } from './plansSlice.js';

/**
 * Declares the plans read RPC via declaration merging (the C1/C2 bus files stay byte-identical).
 * `state.plans_snapshot` is the bus-contract name (`domain.verb`), registered in `host.py` and live.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Fetch the full plans list (with parent/updated_at/char_count). Re-pulled on each `plan`-entity snapshot. */
    'state.plans_snapshot': { params: Record<string, never>; result: PlansSnapshotReply };
  }
}

/**
 * The `state.plans_snapshot` reply. Mirrors the service's plans-snapshot DTO. Only the fields the
 * plans slice projects are typed.
 */
export interface PlansSnapshotReply {
  plans: readonly PlanDto[];
  invalidation_key: string;
}

/** One plan as it crosses the wire. Presentation-free. `parent` is the parent plan's name, or
 * absent/null for a top-level plan. */
export interface PlanDto {
  name: string;
  char_count: number;
  /** ISO-8601 datetime string (Python `datetime.isoformat()`). */
  updated_at: string;
  /** The parent plan's name. Absent or null for a top-level plan. */
  parent?: string | null;
}

/** Project one wire plan into the slice's row. Pure. No formatting/indent — that is the selector's
 * job (rule 2). Normalises an absent `parent` to `null` so the row field is always present. */
function toPlanRow(dto: PlanDto): PlanRow {
  return {
    name: dto.name,
    charCount: dto.char_count,
    updatedAt: dto.updated_at,
    parent: dto.parent ?? null,
  };
}

/**
 * The plans actions, bound to one `BusClient` + store handle. Returned to `../store.ts`.
 */
export interface PlansActions {
  /**
   * Re-pull the plans list and ref-swap *only* the `plans` slice. The sole bus caller for plan
   * data. Rejections land in `plans.error` — never thrown past the action.
   */
  refresh(): Promise<void>;
}

export function createPlansActions(bus: BusClient, store: StoreApi<AppStore>): PlansActions {
  return createRefreshAction(bus, store, {
    key: 'plans',
    method: 'state.plans_snapshot',
    project: (reply) => reply.plans.map(toPlanRow),
  });
}
