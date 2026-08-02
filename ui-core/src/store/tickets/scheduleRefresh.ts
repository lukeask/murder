/**
 * Shared schedule refresh — one `schedule.get` query updating tickets and usage in one transition.
 *
 * Tickets and usage alias {@link createScheduleRefresh}; they do not own separate request state.
 * Writes (`sample`, `setSteering`) stay in usage actions and call this refresh after success.
 */

import type { StoreApi } from 'zustand';
import type { ApplicationClient } from '../../application/ApplicationClient.js';
import { asQueryResult } from '../../application/resultCast.js';
import { createRefreshAction, type RefreshOptions } from '../listSlice.js';
import type { AppStore } from '../store.js';
import { projectScheduleSnapshot } from './scheduleProjection.js';
import type { ScheduleSnapshotReply } from './ticketsActions.js';

export type ScheduleRefresh = {
  refresh(options?: RefreshOptions): Promise<void>;
};

/**
 * One seq/drain for the schedule source. `project` returns a tickets+usage patch applied in one
 * `setState`. Callers may scope `loadingKeys` so a usage write does not flicker the ticket list.
 */
export function createScheduleRefresh(
  bus: ApplicationClient,
  store: StoreApi<AppStore>,
): ScheduleRefresh {
  return createRefreshAction(bus, store, {
    keys: ['tickets', 'usage'],
    method: 'schedule.get',
    project: (reply) =>
      projectScheduleSnapshot(asQueryResult<'schedule.get', ScheduleSnapshotReply>(reply)),
  });
}
