/**
 * Usage actions — the *only* code that calls the bus for usage gauge data (rule 3).
 *
 * A thin shell over the shared {@link createRefreshAction} factory (`../listSlice.js`), exactly
 * like the roster reference (`../roster/rosterActions.js`). The loading→ready/error lifecycle +
 * ref-swap-only-this-key mechanics come from the factory; this file supplies only the three
 * per-domain pieces: the RPC method (+ its reply type, declared below), and the DTO→rows
 * `project` fn.
 *
 * Copy this file to add slice X: swap the RPC method + its reply shape (the `declare module`
 * block), swap the projection, and pass X's slice key + method + project to `createRefreshAction`.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import { createRefreshAction } from '../listSlice.js';
import type { AppStore } from '../store.js';
import type { UsageRow } from './usageSlice.js';

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
 * The usage actions, bound to one `BusClient` + store handle. Returned to `../store.ts`, which
 * hangs them off the store so components dispatch `store.getState().actions.usage.refresh()`.
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
  return createRefreshAction(bus, store, {
    key: 'usage',
    method: 'usage.get_snapshot',
    project: (reply) => reply.gauges.map(toUsageRow),
  });
}
