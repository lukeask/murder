/**
 * Transit actions — the *only* code that calls the bus for transit data (rule 3).
 *
 * One action: `refresh()` — re-pull the whole commit-graph via `state.transit_snapshot` and ref-swap
 * only the `transit` slice (loading → ready/error). Mirrors history's `refresh`, but the transit slice
 * is not a flat `{ rows }` list (it holds `lanes`), so it can't reuse {@link ../listSlice.ts createRefreshAction}
 * — this file has its own small loading→ready/error refresh over the same `setState` discipline.
 *
 * `state.transit_snapshot` is declared via declaration merging (mirroring the history/notes/roster
 * actions) rather than editing the frozen bus files.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { AppStore } from '../store.js';
import type { TransitCommit, TransitLane, TransitState } from './transitSlice.js';

/**
 * Declares the transit read RPC. `state.transit_snapshot` is the bus-contract name (mirrors Python
 * `ServiceReadModel.get_transit_snapshot`, registered in `host.py`).
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /** Fetch the full commit-graph. Re-pulled on each `transit`-entity `state.snapshot`. */
    'state.transit_snapshot': { params: Record<string, never>; result: TransitSnapshotReply };
  }
}

/** One commit as it crosses the wire (Python). Presentation-free, snake_case. */
export interface TransitCommitDto {
  sha: string;
  short: string;
  subject: string;
  body: string;
  ts_epoch: number;
  parents: string[];
}

/** One lane as it crosses the wire (Python). Snake_case; `commits` newest-first incl. pre-fork. */
export interface TransitLaneDto {
  branch: string;
  is_main: boolean;
  worktree_path: string | null;
  head_sha: string;
  fork_sha: string | null;
  commits: readonly TransitCommitDto[];
}

/** The `state.transit_snapshot` reply, mirroring the service's `TransitSnapshot` DTO. */
export interface TransitSnapshotReply {
  lanes: readonly TransitLaneDto[];
  generated_at_epoch: number;
  invalidation_key: string;
}

/** Project one wire commit into the slice's domain commit. Pure: snake→camel, `ts_epoch`→`tsEpoch`. */
function toTransitCommit(dto: TransitCommitDto): TransitCommit {
  return {
    sha: dto.sha,
    short: dto.short,
    subject: dto.subject,
    body: dto.body,
    tsEpoch: dto.ts_epoch,
    parents: dto.parents,
  };
}

/** Project one wire lane into the slice's domain lane. Pure: the single DTO→domain mapping. */
function toTransitLane(dto: TransitLaneDto): TransitLane {
  return {
    branch: dto.branch,
    isMain: dto.is_main,
    worktreePath: dto.worktree_path,
    headSha: dto.head_sha,
    forkSha: dto.fork_sha,
    commits: dto.commits.map(toTransitCommit),
  };
}

/** Project a whole reply into the slice's lanes. Pure (exported for unit-testing). */
export function project(reply: TransitSnapshotReply): readonly TransitLane[] {
  return reply.lanes.map(toTransitLane);
}

/** The transit actions, bound to one `BusClient` + store handle. */
export interface TransitActions {
  /** Re-pull the commit-graph and ref-swap only the `transit` slice. Rejections land in
   * `transit.error` — never thrown past the action (so the invalidation loop stays fire-and-forget). */
  refresh(): Promise<void>;
}

export function createTransitActions(bus: BusClient, store: StoreApi<AppStore>): TransitActions {
  // Per-slice request token — guards against a stale reply clobbering newer lanes when a burst of
  // `transit` invalidations (or a reconnect re-prime) overlaps two refreshes (see listSlice.ts).
  let seq = 0;
  return {
    async refresh(): Promise<void> {
      const token = ++seq;
      // Mark loading by ref-swapping ONLY the transit slice — sibling slices keep their identity.
      store.setState((state) => {
        const current = state.transit;
        return { transit: { ...current, status: 'loading' } };
      });
      // Coalesce a synchronous burst (mirrors listSlice.ts): defer the RPC behind one microtask so a
      // burst of `transit` invalidations bumps `seq` to its final value first; every stale token then
      // short-circuits BEFORE issuing the heavy ~110KB transit_snapshot RPC — only the last call hits
      // the wire. The loading setState above already ran; the surviving call sets the terminal state.
      await Promise.resolve();
      if (token !== seq) return;
      try {
        const reply = await bus.rpc('state.transit_snapshot', {});
        if (token !== seq) return;
        const next: TransitState = { lanes: project(reply), status: 'ready', error: null };
        store.setState({ transit: next });
      } catch (error: unknown) {
        if (token !== seq) return;
        const message = error instanceof Error ? error.message : String(error);
        store.setState((state) => ({
          transit: { ...state.transit, status: 'error', error: message },
        }));
      }
    },
  };
}
