/**
 * Reports slice — domain state for the reports list (panel 3).
 *
 * Copied from {@link ../roster/rosterSlice.js} per the C3 copy recipe (and parallel to
 * {@link ../notes/notesSlice.js} — notes and reports share the same DTO shape, but are two
 * separate concrete slices; no generic base). Row fields mirror Python `ReportSummary`
 * (name + char_count + updated_at). The invalidating entity is `'report'` — NOTE: this value
 * must be added to the `Entity` union in `src/bus/protocol.ts` and the Python
 * `murder/bus/protocol.py` Entity enum (currently absent; see contract gap note in store.ts).
 *
 * Copy this file to add slice X: same steps as the notes/roster recipe.
 */

import type { StateCreator } from 'zustand';
import type { Entity } from '../../bus/protocol.js';
import type { AppStore } from '../store.js';

/**
 * One report as the reports slice cares about it — a faithful, presentation-free projection of
 * the service's ReportSummary DTO. `updatedAt` is the ISO-8601 string from the wire; the
 * selector formats it for display (rule 2).
 */
export interface ReportRow {
  readonly name: string;
  readonly charCount: number;
  /** ISO-8601 string as the wire delivers it. The selector formats it for display. */
  readonly updatedAt: string;
}

/**
 * The reports slice's state. Same shape as {@link NotesState}: `rows` plus `status`/`error`
 * lifecycle fields. Every field is readonly — ref-swapped wholesale on change.
 */
export interface ReportsState {
  readonly rows: readonly ReportRow[];
  readonly status: 'idle' | 'loading' | 'ready' | 'error';
  /** Set when the last refresh rejected; cleared on the next successful load. */
  readonly error: string | null;
}

/**
 * The {@link Entity} key whose `state.snapshot` events invalidate this slice.
 *
 * CONTRACT GAP (flagged): `'report'` is not yet in the Python `Entity` enum in
 * `murder/bus/protocol.py` or the TypeScript `Entity` union in `src/bus/protocol.ts`. It has
 * been added to the TS protocol.ts for this chunk. The Python side and `PROTOCOL_VERSION` must
 * be updated in lockstep when service B13 lands. Until then, `report`-entity events will not
 * arrive from the live bus; the slice will only refresh via direct `actions.reports.refresh()`
 * calls. Tests drive it via `FakeBusClient` which accepts any entity value.
 */
export const REPORTS_INVALIDATING_ENTITY: Entity = 'report';

/** The initial, pre-fetch slice value. */
export const initialReportsState: ReportsState = {
  rows: [],
  status: 'idle',
  error: null,
};

/**
 * Slice factory in Zustand's standard `StateCreator` shape, scoped to the combined
 * {@link AppStore}. Contributes only the `reports` key.
 */
export const createReportsSlice: StateCreator<
  AppStore,
  [],
  [],
  { reports: ReportsState }
> = () => ({
  reports: initialReportsState,
});
