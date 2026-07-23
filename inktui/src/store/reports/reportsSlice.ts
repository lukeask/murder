/**
 * Reports slice — domain state for the reports list (panel 3).
 *
 * Copied from {@link ../roster/rosterSlice.js} per the copy recipe (and parallel to
 * {@link ../notes/notesSlice.js} — notes and reports share the same DTO shape, but are two
 * separate concrete slices). Row fields mirror Python `ReportSummary` (name + char_count +
 * updated_at). Reports are refreshed explicitly because the current application projection
 * protocol has no reports topic.
 *
 * The shared `{ rows, status, error }` mechanics come from the generic {@link ListState} +
 * {@link createListSlice} factory in `../listSlice.js` — this file is a thin shell over it.
 *
 * Copy this file to add slice X: same steps as the notes/roster recipe.
 */

import { createListSlice, initialListState, type ListState } from '../listSlice.js';

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
 * The reports slice's state — the shared {@link ListState} shape specialized to {@link ReportRow}.
 * `rows` plus `status`/`error` lifecycle fields. Selectors read `ReportsState['status']`, so the
 * `'idle' | 'loading' | 'ready' | 'error'` union is part of the contract.
 */
export type ReportsState = ListState<ReportRow>;

/** The initial, pre-fetch slice value. */
export const initialReportsState: ReportsState = initialListState<ReportRow>();

/**
 * Slice factory — the trivial Zustand `StateCreator` that seeds the `reports` key, built from the
 * shared {@link createListSlice}. Contributes only the `reports` key; `../store.ts` composes it.
 */
export const createReportsSlice = createListSlice('reports', initialReportsState);
