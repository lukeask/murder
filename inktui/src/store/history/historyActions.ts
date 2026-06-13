/**
 * History actions — the *only* code that calls the bus for history data (rule 3).
 *
 * Two actions:
 *  - `refresh()` — re-pull the full history feed via `state.history_snapshot` and ref-swap only the
 *    `history` slice. Built on the shared {@link createRefreshAction} factory (loading→ready/error,
 *    ref-swap-only-this-key) like every other list slice.
 *  - `dismiss(itemId)` — submit the `history.dismiss` orchestrator command and OPTIMISTICALLY mark
 *    the row `dismissed` in the slice so it drops from the loose-threads view immediately, without
 *    waiting for the snapshot round-trip. The authoritative refetch (driven by the `history`
 *    `state.snapshot` the dismiss op publishes) reconciles shortly after.
 *
 * `state.history_snapshot` and `history.dismiss` are declared via declaration merging (mirroring the
 * notes/roster actions) rather than editing the frozen bus files.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import { submitCommand } from '../commandSubmit.js';
import { createRefreshAction } from '../listSlice.js';
import type { AppStore } from '../store.js';
import type { HistoryRow, HistoryState } from './historySlice.js';

/**
 * Declares the history read RPC. `state.history_snapshot` is the bus-contract name (mirrors Python
 * `ServiceReadModel.get_history_snapshot`, registered in `host.py`).
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Fetch the full history feed. Re-pulled on each `history`-entity `state.snapshot`. */
    'state.history_snapshot': { params: Record<string, never>; result: HistorySnapshotReply };
  }
}

/** The `state.history_snapshot` reply, mirroring the service's `HistorySnapshot` DTO. */
export interface HistorySnapshotReply {
  items: readonly HistoryItemDto[];
  invalidation_key: string;
}

/** One history item as it crosses the wire (Python `HistoryItemSummary`). Presentation-free. */
export interface HistoryItemDto {
  item_id: string;
  text: string;
  target: string;
  ts: string;
  status: string;
  harness: string | null;
  conversation_status: string;
  resumable: boolean;
}

/** Project one wire item into the slice's row. Pure: the single DTO→domain mapping. */
function toHistoryRow(dto: HistoryItemDto): HistoryRow {
  return {
    itemId: dto.item_id,
    text: dto.text,
    target: dto.target,
    ts: dto.ts,
    status: dto.status,
    harness: dto.harness,
    conversationStatus: dto.conversation_status,
    resumable: dto.resumable,
  };
}

/** The history actions, bound to one `BusClient` + store handle. */
export interface HistoryActions {
  /** Re-pull the history feed and ref-swap only the `history` slice. Rejections land in
   * `history.error` — never thrown past the action. */
  refresh(): Promise<void>;
  /** Dismiss one item (terminal status). Optimistically marks the row `dismissed` in the slice, then
   * submits the `history.dismiss` command. Rejections are swallowed (the optimistic state is
   * reconciled by the authoritative refetch the dismiss op publishes). */
  dismiss(itemId: string): Promise<void>;
}

export function createHistoryActions(bus: BusClient, store: StoreApi<AppStore>): HistoryActions {
  const { refresh } = createRefreshAction(bus, store, {
    key: 'history',
    method: 'state.history_snapshot',
    project: (reply) => reply.items.map(toHistoryRow),
  });

  return {
    refresh,
    async dismiss(itemId: string): Promise<void> {
      // Optimistic: ref-swap ONLY the history slice, marking the matching row dismissed so it drops
      // from the loose-threads view immediately (the invalidation-granularity contract).
      store.setState((state) => {
        const current = state.history as HistoryState;
        const rows = current.rows.map((row) =>
          row.itemId === itemId ? { ...row, status: 'dismissed' } : row,
        );
        return { history: { ...current, rows } };
      });
      try {
        await submitCommand(bus, 'history.dismiss', { item_id: itemId });
      } catch {
        // Swallow: the authoritative refetch (the dismiss op publishes a `history` snapshot key, and
        // a failed command leaves the server state unchanged) reconciles the optimistic row.
      }
    },
  };
}
