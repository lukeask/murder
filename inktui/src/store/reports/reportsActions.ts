/**
 * Reports actions — the *only* code that calls the bus for reports data (rule 3).
 *
 * Copied from {@link ../notes/notesActions.js}. Changes vs. notes:
 *  - RPC is `state.reports_snapshot` (bus-contract naming; LIVE — registered in `host.py`).
 *  - Reply shape mirrors Python `ReportsSnapshot` (reports[] with name/char_count/updated_at).
 *  - `declare module` augments `RpcMethods` with `'state.reports_snapshot'` (distinct key — never
 *    redeclare an existing one).
 *  - Passes the `reports` slice key to `createRefreshAction`.
 *
 * The loading→ready/error + ref-swap-only-this-key mechanics come from the shared
 * {@link createRefreshAction} factory in `../listSlice.js`.
 */

import type { StoreApi } from 'zustand';
import type { ApplicationClient } from '../../application/ApplicationClient.js';
import { asQueryResult } from '../../application/resultCast.js';
import { createRefreshAction } from '../listSlice.js';
import type { AppStore } from '../store.js';
import type { ReportRow } from './reportsSlice.js';

/**
 * Declares the reports read RPC via declaration merging. `state.reports_snapshot` is the bus-contract
 * name (`domain.verb`, mirrors Python `RuntimeClient.get_reports_snapshot`). LIVE — registered in
 * `host.py`, per the contract's "view → service = RPC methods" rule.
 */


/**
 * The `state.reports_snapshot` reply, mirroring the service's `ReportsSnapshot` DTO from
 * `murder/app/protocol/read_models.py`.
 */
export interface ReportsSnapshotReply {
  reports: readonly ReportDto[];
  invalidation_key: string;
}

/** One report as it crosses the wire (Python `ReportSummary`). Presentation-free. */
export interface ReportDto {
  name: string;
  char_count: number;
  /** ISO-8601 datetime string (Python `datetime.isoformat()`). */
  updated_at: string;
}

/** Project one wire report into the slice's row. Pure DTO→domain mapping; no formatting. */
function toReportRow(dto: ReportDto): ReportRow {
  return {
    name: dto.name,
    charCount: dto.char_count,
    updatedAt: dto.updated_at,
  };
}

/**
 * The reports actions, bound to one `ApplicationClient` + store handle.
 */
export interface ReportsActions {
  /**
   * Re-pull the reports list and ref-swap *only* the `reports` slice. The sole bus caller for
   * report data. Rejections land in `reports.error` — never thrown past the action.
   */
  refresh(): Promise<void>;
}

export function createReportsActions(bus: ApplicationClient, store: StoreApi<AppStore>): ReportsActions {
  return createRefreshAction(bus, store, {
    key: 'reports',
    method: 'reports.list',
    project: (reply) =>
      asQueryResult<'reports.list', ReportsSnapshotReply>(reply).reports.map(toReportRow),
  });
}
