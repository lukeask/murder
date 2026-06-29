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
import type { FavoritesState } from '../store/favorites/favoritesSlice.js';
import type { NoteRow, NotesState } from '../store/notes/notesSlice.js';
import { isInFavoriteSet, stableSortStarredFirst } from './favoritesSelectors.js';
import { type ListSurfaceStatus, toListSurfaceStatus } from './listViewModel.js';
import { formatCharCount, formatUpdatedAt } from './resourceMeta.js';

/**
 * One note row as the component paints it: display-ready strings for both lines of the two-line
 * entry. All formatting lives here, not in the component or the store (rule 2).
 */
export interface NoteRowView {
  readonly name: string;
  /** Character count formatted for display (e.g. `"1,234 chars"`). */
  readonly charCount: string;
  /** `updated_at` formatted `Mon. dd HH:MM` (e.g. `Jun. 10 09:32`). */
  readonly updatedAt: string;
  /** Whether this note is starred (in the explicit favorite set) — the panel renders a marker. */
  readonly starred: boolean;
}

/** The whole notes list, render-ready. Parallel to {@link RosterView}. */
export interface NotesView {
  readonly rows: readonly NoteRowView[];
  readonly status: ListSurfaceStatus;
  readonly error: string | null;
  readonly isEmpty: boolean;
}

/** Project one domain row into its presentation tuple. */
function toNoteRowView(row: NoteRow, starred: boolean): NoteRowView {
  return {
    name: row.name,
    charCount: formatCharCount(row.charCount),
    updatedAt: formatUpdatedAt(row.updatedAt),
    starred,
  };
}

/** Sort comparator: most recently updated first (descending by `updatedAt` ISO string — ISO
 * strings sort lexicographically in date order), tiebreak by name for a stable order. */
function byUpdatedAtDescThenName(a: NoteRow, b: NoteRow): number {
  const cmp = b.updatedAt.localeCompare(a.updatedAt);
  return cmp !== 0 ? cmp : a.name.localeCompare(b.name);
}

/**
 * The pure view-model transform. Orders by recency, then floats starred notes to the top (stable —
 * the recency order is preserved within the starred and unstarred blocks; spec › "Starred shown at
 * top"). Never mutates the slice's readonly array. Same input → same output, no React/store/bus.
 */
export function selectNotesView(state: NotesState, favorites: FavoritesState): NotesView {
  const byRecency = [...state.rows].sort(byUpdatedAtDescThenName);
  const ordered = stableSortStarredFirst(
    byRecency,
    (row) => row.name,
    (id) => isInFavoriteSet(favorites, id),
  );
  const rows = ordered.map((row) => toNoteRowView(row, isInFavoriteSet(favorites, row.name)));
  return {
    rows,
    status: toListSurfaceStatus(state.status),
    error: state.error,
    isEmpty: rows.length === 0,
  };
}

/**
 * Component-facing hook: memoizes {@link selectNotesView} on the (notes, favorites) slice identities.
 *   `const view = useNotesView(useAppStore((s) => s.notes), useAppStore((s) => s.favorites));`
 */
export function useNotesView(state: NotesState, favorites: FavoritesState): NotesView {
  return useMemo(() => selectNotesView(state, favorites), [state, favorites]);
}
