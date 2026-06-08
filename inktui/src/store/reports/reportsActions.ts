/**
 * Reports actions — the *only* code that calls the bus for reports data (rule 3).
 *
 * Copied from {@link ../notes/notesActions.js}. Changes vs. notes:
 *  - RPC is `report.get_snapshot` (NOT yet on the live bus; modeled per contract naming).
 *  - Reply shape mirrors Python `ReportsSnapshot` (reports[] with name/char_count/updated_at).
 *  - `declare module` augments `RpcMethods` with `'report.get_snapshot'` (distinct key — never
 *    redeclare an existing one).
 *  - Ref-swaps `state.reports`, not `state.notes`.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { AppStore } from '../store.js';
import type { ReportRow, ReportsState } from './reportsSlice.js';

/**
 * Declares the reports read RPC via declaration merging. `report.get_snapshot` is the bus-contract
 * name (`domain.verb`, mirrors Python `RuntimeClient.get_reports_snapshot`). NOT yet on the live
 * bus — modeled here per the contract's "view → service = RPC methods" rule. Confirm name/shape
 * when service B13 lands. CONTRACT GAP: also requires the `'report'` Entity to be added to the
 * Python protocol (see reportsSlice.ts).
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Fetch the full reports list. Re-pulled on each `report`-entity `state.snapshot`. */
    'report.get_snapshot': { params: Record<string, never>; result: ReportsSnapshotReply };
  }
}

/**
 * The `report.get_snapshot` reply, mirroring the service's `ReportsSnapshot` DTO from
 * `murder/app/service/client_api.py`.
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
 * The reports actions, bound to one `BusClient` + store handle.
 */
export interface ReportsActions {
  /**
   * Re-pull the reports list and ref-swap *only* the `reports` slice. The sole bus caller for
   * report data. Rejections land in `reports.error` — never thrown past the action.
   */
  refresh(): Promise<void>;
}

export function createReportsActions(bus: BusClient, store: StoreApi<AppStore>): ReportsActions {
  return {
    async refresh(): Promise<void> {
      store.setState((state) => ({ reports: { ...state.reports, status: 'loading' } }));
      try {
        const reply = await bus.rpc('report.get_snapshot', {});
        const rows = reply.reports.map(toReportRow);
        const next: ReportsState = { rows, status: 'ready', error: null };
        store.setState({ reports: next });
      } catch (error: unknown) {
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => ({
          reports: { ...state.reports, status: 'error', error: message },
        }));
      }
    },
  };
}
