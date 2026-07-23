/**
 * Transit slice — domain state for the git commit-graph panel (panel 8, ctrl+8).
 *
 * The transit view is a read model over `main` + every `.murder/worktrees` branch: each LANE is a
 * branch (its head + its commits, newest-first, INCLUDING pre-fork shared ancestry), and the panel
 * draws per-lane horizontal railways with commit stations + relative-age markers. Like the other
 * slices it holds raw, wire-faithful domain data only — all geometry/formatting (station windowing,
 * railway glyph strings, age markers) lives in the selector (rule 2).
 *
 * Unlike the four list quads this is NOT a flat `{ rows }` list, so it does not use
 * {@link ../listSlice.ts createListSlice}: it holds an array of {@link TransitLane}s plus the load
 * `status`/`error`. The cursor (selected lane + commit) lives in the component, not here (rule 1).
 * The slice holds state only; the `setLanes`-style mutation is done by the action layer
 * (`./transitActions.ts`) calling `set` through the store handle, keeping the bus out of this file.
 */

import type { StateCreator } from 'zustand';
import type { AppStore } from '../store.js';

/** One commit as the slice cares about it — a presentation-free projection of the wire DTO. `tsEpoch`
 * is unix seconds (the selector formats relative age); `parents` are the parent shas (graph edges). */
export interface TransitCommit {
  readonly sha: string;
  readonly short: string;
  readonly subject: string;
  readonly body: string;
  /** Commit time as unix epoch SECONDS (the selector floors relative age against `now`). */
  readonly tsEpoch: number;
  readonly parents: readonly string[];
}

/** One lane = a branch (trunk `main` or a `.murder/worktrees` branch). `commits` are newest-first and
 * include pre-fork shared ancestry (so a duration-jump walks back across the fork into `main`). */
export interface TransitLane {
  readonly branch: string;
  readonly isMain: boolean;
  readonly worktreePath: string | null;
  readonly headSha: string;
  /** `git merge-base main <branch>` — where this lane diverges from main. `null` for main itself. */
  readonly forkSha: string | null;
  readonly commits: readonly TransitCommit[];
}

/**
 * The transit slice's state: the lanes + the load lifecycle (mirroring {@link ../listSlice.ts ListState}'s
 * `status` union so a component can tell "not fetched yet" from "fetched, empty"). Every field is
 * readonly — the slice is ref-swapped wholesale on change, never mutated in place.
 */
export interface TransitState {
  readonly lanes: readonly TransitLane[];
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last refresh rejected; cleared on the next successful load. */
  readonly error: string | null;
}

/** The initial, pre-fetch slice value. A fresh store has not talked to the bus → `idle`. */
export const initialTransitState: TransitState = { lanes: [], status: 'idle', error: null };

/** Slice factory — seeds the `transit` key with its idle initial state. The slice holds state, not
 * actions: mutation is done by the action layer (see `./transitActions.ts`), keeping the bus
 * dependency out of this framework-agnostic file (rule 4). */
export const createTransitSlice: StateCreator<
  AppStore,
  [],
  [],
  { transit: TransitState }
> = () => ({
  transit: initialTransitState,
});
