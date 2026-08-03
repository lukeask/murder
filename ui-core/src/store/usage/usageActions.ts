/**
 * Usage actions — the *only* code that calls the bus for usage gauge data (rule 3), via the shared
 * schedule refresh for reads, plus write workflows for sample/steering.
 *
 * Usage has NO dedicated RPC — gauges are embedded in `schedule.get`. {@link refresh} is an alias
 * of the shared schedule refresh. {@link sample} and {@link setSteering} remain command workflows
 * that call the shared refresh after a successful write; writes stay out of scheduleProjection.
 */

import type { StoreApi } from 'zustand';
import type { ApplicationClient } from '../../application/ApplicationClient.js';
import { submitCommand } from '../commandSubmit.js';
import type { RefreshOptions } from '../listSlice.js';
import type { AppStore } from '../store.js';
import { createScheduleRefresh, type ScheduleRefresh } from '../tickets/scheduleRefresh.js';
import { toastStore } from '../toast/toastStore.js';

/**
 * The usage actions, bound to one `ApplicationClient` + store handle. Returned to `../store.ts`, which
 * hangs them off the store so components dispatch `store.getState().actions.usage.refresh()`.
 */
export interface UsageActions {
  /**
   * Re-pull the schedule snapshot and update usage (and tickets) via the shared schedule refresh.
   * Alias of the shared refresh — no separate request state. Pass `loadingKeys: ['usage']` after a
   * usage write so the ticket list does not flicker or take a refresh-failure error.
   */
  refresh(options?: RefreshOptions): Promise<void>;
  /**
   * Ask the backend usage-probe worker to collect fresh harness snapshots, then re-pull the gauges.
   * This is intentionally separate from `refresh()`: refresh is read-only, sample mutates the
   * snapshot table via the worker command path. Loading is scoped to `usage` so the ticket list
   * does not flicker.
   */
  sample(): Promise<void>;
  /**
   * RT5: set a harness's scheduler steering (`'auto' | 'pause' | 'prefer'`) via the
   * `scheduler.set_steering` command on the `scheduler` worker, then refetch (belt-and-braces:
   * the backend also emits a `queue_row` invalidation). Errors route into `usage.error` like a
   * failed refresh — never thrown past the action (the keypress handler stays fire-and-forget).
   * Preserves the existing error asymmetry vs sample: sets only `error`, not `status: 'error'`.
   */
  setSteering(harness: string, steering: string): Promise<void>;
}

export function createUsageActions(
  bus: ApplicationClient,
  store: StoreApi<AppStore>,
  scheduleRefresh?: ScheduleRefresh,
): UsageActions {
  const shared = scheduleRefresh ?? createScheduleRefresh(bus, store);
  return {
    refresh: (options) => shared.refresh(options),
    async sample(): Promise<void> {
      // Do not manage usage.status here — scope loading to usage via the shared refresh so tickets
      // stay undisturbed.
      try {
        await submitCommand(bus, 'state.harness_usage.sample', { trigger: 'manual_refresh' });
        await shared.refresh({ loadingKeys: ['usage'] });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        store.setState((s) => ({ usage: { ...s.usage, status: 'error', error: message } }));
        toastStore
          .getState()
          .push(`usage sample failed: ${message}`, { severity: 'error', ttlMs: 12000 });
      }
    },
    async setSteering(harness: string, steering: string): Promise<void> {
      try {
        await submitCommand(bus, 'scheduler.set_steering', { harness, steering });
        await shared.refresh({ loadingKeys: ['usage'] });
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        // Error asymmetry preserved: setSteering sets only `error`, not status:'error'.
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
