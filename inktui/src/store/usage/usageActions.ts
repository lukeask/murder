/**
 * Usage actions — the *only* code that calls the bus for usage gauge data (rule 3).
 *
 * A thin shell over the shared {@link createRefreshAction} factory (`../listSlice.js`), exactly
 * like the roster reference (`../roster/rosterActions.js`). The loading→ready/error lifecycle +
 * ref-swap-only-this-key mechanics come from the factory; this file supplies only the per-domain
 * pieces: the RPC method and the DTO→rows `project` fn.
 *
 * Usage has NO dedicated RPC on the live bus — the gauges are embedded in the LIVE
 * `state.schedule_snapshot` reply (`ScheduleSnapshot.usage_gauges`). This slice therefore calls the
 * same `state.schedule_snapshot` method as the tickets slice and projects `usage_gauges`. The RPC
 * key + its reply type (`ScheduleSnapshotReply`/`ScheduleUsageGaugeDto`) are declared ONCE in
 * `../tickets/ticketsActions.ts`; this file imports the gauge type and never re-declares the key.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
// `state.schedule_snapshot` is declared once, by the tickets slice; usage consumes its reply type
// and the `ScheduleUsageGaugeDto` shape without re-declaring the key (a second `declare module`
// augmentation with a different `result` would be a TS 2717 collision). Usage data is embedded in
// the schedule snapshot's `usage_gauges` — there is no separate usage RPC on the live bus.
import { submitCommand } from '../commandSubmit.js';
import { createRefreshAction } from '../listSlice.js';
import type { AppStore } from '../store.js';
import type { ScheduleUsageGaugeDto } from '../tickets/ticketsActions.js';
import { toastStore } from '../toast/toastStore.js';
import type { UsageRow } from './usageSlice.js';

/** Project one wire gauge into the slice's row. Pure: single DTO→domain mapping point. */
function toUsageRow(dto: ScheduleUsageGaugeDto): UsageRow {
  return {
    harness: dto.harness,
    windowKey: dto.window_key,
    pct: dto.pct,
    tUntilResetMinutes: dto.t_until_reset_minutes,
    tPeriodMinutes: dto.t_period_minutes ?? 0,
    steering: dto.steering ?? 'auto',
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
  /**
   * RT5: set a harness's scheduler steering (`'auto' | 'pause' | 'prefer'`) via the
   * `scheduler.set_steering` command on the `scheduler` worker, then refetch (belt-and-braces:
   * the backend also emits a `queue_row` invalidation). Errors route into `usage.error` like a
   * failed refresh — never thrown past the action (the keypress handler stays fire-and-forget).
   */
  setSteering(harness: string, steering: string): Promise<void>;
}

export function createUsageActions(bus: BusClient, store: StoreApi<AppStore>): UsageActions {
  const { refresh } = createRefreshAction(bus, store, {
    key: 'usage',
    method: 'state.schedule_snapshot',
    project: (reply) => reply.usage_gauges.map(toUsageRow),
  });
  return {
    refresh,
    async setSteering(harness: string, steering: string): Promise<void> {
      try {
        await submitCommand(
          bus,
          'scheduler.set_steering',
          { harness, steering },
          {
            targetWorker: 'scheduler',
          },
        );
        await refresh();
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        store.setState((s) => ({ usage: { ...s.usage, error: message } }));
        // Also surface a toast: `usage.error` is not reliably rendered by a view, and steering is a
        // keypress-driven write — a silent failure would leave the user thinking it took effect.
        toastStore
          .getState()
          .push(`steering failed: ${message}`, { severity: 'error', ttlMs: 12000 });
      }
    },
  };
}
