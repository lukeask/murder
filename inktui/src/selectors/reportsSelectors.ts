/**
 * Reports view-models — the selector (rule 2: presentation lives here, never in the store).
 *
 * Copied from {@link ./notesSelectors.js} — notes and reports share the same DTO shape
 * (name + char_count + updated_at) and the same presentation logic. Two separate files (not
 * a shared generic) because they are two separate slices, and future divergence (e.g. a
 * reports-specific sort) stays local without affecting notes.
 */

import { useMemo } from 'react';
import type { FavoritesState } from '../store/favorites/favoritesSlice.js';
import type { ReportRow, ReportsState } from '../store/reports/reportsSlice.js';
import { isInFavoriteSet, stableSortStarredFirst } from './favoritesSelectors.js';
import { formatCharCount, formatUpdatedAt } from './resourceMeta.js';

/**
 * One report row as the component paints it: display-ready strings for both lines of the
 * two-line entry. All formatting lives here, not in the component or the store (rule 2).
 */
export interface ReportRowView {
  readonly name: string;
  /** Character count formatted for display. */
  readonly charCount: string;
  /** `updated_at` formatted `Mon. dd HH:MM` (e.g. `Jun. 10 09:32`). */
  readonly updatedAt: string;
  /** Whether this report is starred (in the explicit favorite set). */
  readonly starred: boolean;
}

/** The whole reports list, render-ready. Parallel to {@link NotesView}. */
export interface ReportsView {
  readonly rows: readonly ReportRowView[];
  readonly status: ReportsState['status'];
  readonly error: string | null;
  readonly isEmpty: boolean;
}

/** Project one domain row into its presentation tuple. */
function toReportRowView(row: ReportRow, starred: boolean): ReportRowView {
  return {
    name: row.name,
    charCount: formatCharCount(row.charCount),
    updatedAt: formatUpdatedAt(row.updatedAt),
    starred,
  };
}

/** Sort comparator: most recently updated first, tiebreak by name for a stable order. */
function byUpdatedAtDescThenName(a: ReportRow, b: ReportRow): number {
  const cmp = b.updatedAt.localeCompare(a.updatedAt);
  return cmp !== 0 ? cmp : a.name.localeCompare(b.name);
}

/**
 * The pure view-model transform. Orders by recency, then floats starred reports to the top (stable —
 * spec › "Starred shown at top"). Same input → same output, no React/store/bus.
 */
export function selectReportsView(state: ReportsState, favorites: FavoritesState): ReportsView {
  const byRecency = [...state.rows].sort(byUpdatedAtDescThenName);
  const ordered = stableSortStarredFirst(
    byRecency,
    (row) => row.name,
    (id) => isInFavoriteSet(favorites, id),
  );
  const rows = ordered.map((row) => toReportRowView(row, isInFavoriteSet(favorites, row.name)));
  return {
    rows,
    status: state.status,
    error: state.error,
    isEmpty: rows.length === 0,
  };
}

/**
 * Component-facing hook: memoizes {@link selectReportsView} on the (reports, favorites) identities.
 */
export function useReportsView(state: ReportsState, favorites: FavoritesState): ReportsView {
  return useMemo(() => selectReportsView(state, favorites), [state, favorites]);
}
