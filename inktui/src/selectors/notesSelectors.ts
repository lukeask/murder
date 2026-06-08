/**
 * Notes view-models — the selector (rule 2: presentation lives here, never in the store).
 *
 * Copied from {@link ./rosterSelectors.js} per the C3 copy recipe. Changes vs. roster:
 *  - Row type is {@link NoteRowView} (name, charCount formatted, updatedAt formatted).
 *  - Sort: by `updatedAt` descending (most recently updated first), then by name for a stable
 *    tiebreak. Documents don't have a status-rank; recency is the natural document order.
 *  - Formatting: `charCount` formatted as a compact string; `updatedAt` sliced to 16 chars and
 *    the `T` separator replaced with a space, mirroring Python `[:16].replace("T"," ")`.
 *  - Two-line entry: line 1 = name, line 2 = char count · updated time.
 *
 * Two layers (same as roster):
 *  - **Pure transform** `selectNotesView` — no React, unit-testable, callable from any frontend.
 *  - **`useMemo` hook** `useNotesView` — component-facing wrapper that memoizes on slice identity.
 */

import { useMemo } from 'react';
import type { NoteRow, NotesState } from '../store/notes/notesSlice.js';

/**
 * One note row as the component paints it: display-ready strings for both lines of the two-line
 * entry. All formatting lives here, not in the component or the store (rule 2).
 */
export interface NoteRowView {
  readonly name: string;
  /** Character count formatted for display (e.g. `"1 234 chars"`). */
  readonly charCount: string;
  /** `updated_at` formatted as `YYYY-MM-DD HH:MM` (ISO with T replaced by space, 16 chars). */
  readonly updatedAt: string;
}

/** The whole notes list, render-ready. Parallel to {@link RosterView}. */
export interface NotesView {
  readonly rows: readonly NoteRowView[];
  readonly status: NotesState['status'];
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
function toNoteRowView(row: NoteRow): NoteRowView {
  return {
    name: row.name,
    charCount: formatCharCount(row.charCount),
    updatedAt: formatUpdatedAt(row.updatedAt),
  };
}

/** Sort comparator: most recently updated first (descending by `updatedAt` ISO string — ISO
 * strings sort lexicographically in date order), tiebreak by name for a stable order. */
function byUpdatedAtDescThenName(a: NoteRow, b: NoteRow): number {
  const cmp = b.updatedAt.localeCompare(a.updatedAt);
  return cmp !== 0 ? cmp : a.name.localeCompare(b.name);
}

/**
 * The pure view-model transform. Sorts a copy (never mutates the slice's readonly array) and
 * projects each row. Same input → same output, no React, no store, no bus.
 */
export function selectNotesView(state: NotesState): NotesView {
  const rows = [...state.rows].sort(byUpdatedAtDescThenName).map(toNoteRowView);
  return {
    rows,
    status: state.status,
    error: state.error,
    isEmpty: rows.length === 0,
  };
}

/**
 * Component-facing hook: memoizes {@link selectNotesView} on the slice identity. A component does:
 *   `const view = useNotesView(useAppStore((s) => s.notes));`
 */
export function useNotesView(state: NotesState): NotesView {
  return useMemo(() => selectNotesView(state), [state]);
}
