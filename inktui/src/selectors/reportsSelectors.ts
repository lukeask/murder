/**
 * Reports view-models — the selector (rule 2: presentation lives here, never in the store).
 *
 * Copied from {@link ./notesSelectors.js} — notes and reports share the same DTO shape
 * (name + char_count + updated_at) and the same presentation logic. Two separate files (not
 * a shared generic) because they are two separate slices, and future divergence (e.g. a
 * reports-specific sort) stays local without affecting notes.
 */

import { useMemo } from 'react';
import type { ReportRow, ReportsState } from '../store/reports/reportsSlice.js';

/**
 * One report row as the component paints it: display-ready strings for both lines of the
 * two-line entry. All formatting lives here, not in the component or the store (rule 2).
 */
export interface ReportRowView {
  readonly name: string;
  /** Character count formatted for display. */
  readonly charCount: string;
  /** `updated_at` formatted as `YYYY-MM-DD HH:MM`. */
  readonly updatedAt: string;
}

/** The whole reports list, render-ready. Parallel to {@link NotesView}. */
export interface ReportsView {
  readonly rows: readonly ReportRowView[];
  readonly status: ReportsState['status'];
  readonly error: string | null;
  readonly isEmpty: boolean;
}

/** Format an ISO-8601 datetime string to `YYYY-MM-DD HH:MM`. Mirrors Python `[:16].replace("T"," ")`. */
function formatUpdatedAt(iso: string): string {
  return iso.slice(0, 16).replace('T', ' ');
}

/** Format a character count as a compact, human-readable display string. */
function formatCharCount(n: number): string {
  return `${n.toLocaleString()} chars`;
}

/** Project one domain row into its presentation tuple. */
function toReportRowView(row: ReportRow): ReportRowView {
  return {
    name: row.name,
    charCount: formatCharCount(row.charCount),
    updatedAt: formatUpdatedAt(row.updatedAt),
  };
}

/** Sort comparator: most recently updated first, tiebreak by name for a stable order. */
function byUpdatedAtDescThenName(a: ReportRow, b: ReportRow): number {
  const cmp = b.updatedAt.localeCompare(a.updatedAt);
  return cmp !== 0 ? cmp : a.name.localeCompare(b.name);
}

/**
 * The pure view-model transform. Sorts a copy and projects each row.
 */
export function selectReportsView(state: ReportsState): ReportsView {
  const rows = [...state.rows].sort(byUpdatedAtDescThenName).map(toReportRowView);
  return {
    rows,
    status: state.status,
    error: state.error,
    isEmpty: rows.length === 0,
  };
}

/**
 * Component-facing hook: memoizes {@link selectReportsView} on the slice identity. A component does:
 *   `const view = useReportsView(useAppStore((s) => s.reports));`
 */
export function useReportsView(state: ReportsState): ReportsView {
  return useMemo(() => selectReportsView(state), [state]);
}
