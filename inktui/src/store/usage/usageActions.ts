/**
 * Usage actions — the *only* code that calls the bus for usage gauge data (rule 3).
 *
 * Copied from {@link ../roster/rosterActions.js} per the C3 copy recipe. Changes vs. the roster:
 *  - RPC is `usage.get_snapshot` (modeled per bus-contract naming `domain.verb`). NOT yet on the
 *    live bus — the Python service exposes usage gauges embedded in `ScheduleSnapshot.usage_gauges`
 *    (from `get_schedule_snapshot()`), not as a standalone RPC. This models the intended dedicated
 *    surface. Confirm name/shape and implement server side when B13 lands.
 *  - Reply shape is {@link UsageSnapshotReply} (mirrors `UsageGaugeSummary` from
 *    `murder/app/service/client_api.py`).
 *  - Projection is `toUsageRow` (wire DTO → presentation-free {@link UsageRow}).
 *  - Ref-swaps `state.usage`, not `state.roster`.
 *  - `declare module` augments `RpcMethods` with `'usage.get_snapshot'` — distinct from every
 *    other slice's key; never redeclares an existing one.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { AppStore } from '../store.js';
import type { UsageRow, UsageState } from './usageSlice.js';

/**
 * Declares the usage read RPC via declaration merging. `usage.get_snapshot` is the bus-contract
 * name (`domain.verb`). NOT yet on the live bus — usage data is currently embedded in the
 * schedule snapshot (`ScheduleSnapshot.usage_gauges`). This models the dedicated surface the
 * bus contract implies. Augments `RpcMethods` with its OWN key (never redeclares an existing).
 *
 * NOTE FOR THE SERVICE: implement `usage.get_snapshot` as a standalone RPC that returns usage
 * gauges without the full schedule payload. When B13 lands, confirm or correct the name/shape
 * and update this module + the service in lockstep.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /**
     * Fetch usage gauge summary. Re-pulled on each `agent`-entity `state.snapshot` (usage
     * changes when agents run). NOT yet live — modeled per bus-contract convention.
     */
    'usage.get_snapshot': { params: Record<string, never>; result: UsageSnapshotReply };
  }
}

/**
 * The `usage.get_snapshot` reply, mirroring the service's `UsageGaugeSummary` DTO from
 * `murder/app/service/client_api.py`. Only the fields the usage slice projects are typed;
 * the wire may carry more.
 */
export interface UsageSnapshotReply {
  gauges: readonly UsageGaugeDto[];
  invalidation_key: string;
}

/**
 * One usage gauge row as it crosses the wire (Python `UsageGaugeSummary`). Presentation-free.
 * `pct` is a raw float (0–100+); formatting and bar-width are the selector's job (rule 2).
 */
export interface UsageGaugeDto {
  harness: string;
  window_key: string;
  pct: number;
  t_until_reset_minutes: number;
  t_period_minutes?: number;
}

/** Project one wire gauge into the slice's row. Pure: single DTO→domain mapping point. */
function toUsageRow(dto: UsageGaugeDto): UsageRow {
  return {
    harness: dto.harness,
    windowKey: dto.window_key,
    pct: dto.pct,
    tUntilResetMinutes: dto.t_until_reset_minutes,
    tPeriodMinutes: dto.t_period_minutes ?? 0,
  };
}

/**
 * The usage actions, bound to one `BusClient` + store handle. Returned to `../store.ts`,
 * which hangs them off the store so components dispatch `store.getState().actions.usage.refresh()`.
 */
export interface UsageActions {
  /**
   * Re-pull the usage gauges and ref-swap *only* the `usage` slice. The sole bus caller for
   * usage data. Idempotent; concurrent calls are last-write (latest reply wins). Rejections
   * land in `usage.error` — never thrown past the action (the invalidation loop stays
   * fire-and-forget).
   */
  refresh(): Promise<void>;
}

export function createUsageActions(bus: BusClient, store: StoreApi<AppStore>): UsageActions {
  return {
    async refresh(): Promise<void> {
      // Ref-swap ONLY the usage slice — sibling slices keep identity (invalidation-granularity
      // contract). Mirrors the roster action's loading→ready/error lifecycle exactly.
      store.setState((state) => ({ usage: { ...state.usage, status: 'loading' } }));
      try {
        const reply = await bus.rpc('usage.get_snapshot', {});
        const rows = reply.gauges.map(toUsageRow);
        const next: UsageState = { rows, status: 'ready', error: null };
        store.setState({ usage: next });
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => ({
          usage: { ...state.usage, status: 'error', error: message },
        }));
      }
    },
  };
}
