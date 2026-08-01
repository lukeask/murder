/**
 * Roster view-models — the reference selector (rule 2: presentation lives here, never in the store).
 *
 * A selector turns a presentation-free slice ({@link RosterState}) into render-ready data: it sorts,
 * truncates to a column width, and builds the column tuple a component paints. Keeping this out of
 * the store is what lets a future React-DOM client reuse the same slice with its own (wider) columns.
 *
 * Two layers, deliberately:
 *  - **Pure transforms** (`selectRosterView`, the helpers) — no React, unit-testable in isolation,
 *    callable from any frontend. THIS is what C3's selector test asserts on.
 *  - **A `useMemo` hook** (`useRosterView`) — the component-facing wrapper that memoizes the pure
 *    transform on its inputs, so a component re-render with an unchanged slice does no re-sort. The
 *    hook lives here (not `src/hooks/`) because it is presentation, bound to this domain's columns.
 *
 * Copy this file to add slice X's view-model: swap the row type, the sort key, and the column fields.
 */

import { useMemo } from 'react';
import type { RosterRow, RosterState } from '../store/roster/rosterSlice.js';

/**
 * One roster row as the component paints it: a fixed-width, sort-ordered presentation tuple. All
 * strings are display-ready (truncated, sentinel-filled) so the component does zero formatting.
 */
export interface RosterRowView {
  readonly agentId: string;
  /** Crow display name, falling back to the agent id when no session name is set. */
  readonly name: string;
  readonly status: string;
  readonly harness: string;
  /** Model, basename-only and truncated to {@link MODEL_WIDTH}; `'—'` when absent. */
  readonly model: string;
}

/** The whole roster, render-ready: rows in display order plus the load flags a component branches on
 * for empty/loading/error chrome (lifted from the slice so the component reads one object). */
export interface RosterView {
  readonly rows: readonly RosterRowView[];
  readonly status: RosterState['status'];
  readonly error: string | null;
  readonly isEmpty: boolean;
}

/** Column width budget for the model cell. A presentation constant — it belongs with the selector,
 * not the store, because it is a property of how this panel is drawn. */
export const MODEL_WIDTH = 18;

/** Display ordering by liveness: the states a user acts on first sort to the top. Unlisted states
 * fall to the end. Mirrors the Python roster's `_STATUS_SORT_RANK`. */
const STATUS_RANK: Readonly<Record<string, number>> = {
  escalating: 0,
  blocked: 1,
  running: 2,
  idle: 3,
  failed: 4,
};
const STATUS_RANK_FALLBACK = 99;

/** Truncate to `width`, marking elision with an ellipsis so the cut is visible, not silent. */
function truncate(text: string, width: number): string {
  return text.length <= width ? text : `${text.slice(0, width - 1)}…`;
}

/** Strip a `provider/model` prefix to the bare model name — the part that carries information in a
 * narrow cell. Mirrors the Python `_compact_model` basename step. */
function modelBasename(model: string | null): string {
  const raw = (model ?? '').trim();
  if (raw === '') {
    return '—';
  }
  const slash = raw.lastIndexOf('/');
  const base = slash === -1 ? raw : raw.slice(slash + 1);
  return truncate(base, MODEL_WIDTH);
}

/** Project one domain row into its presentation tuple. */
function toRowView(row: RosterRow): RosterRowView {
  return {
    agentId: row.agentId,
    name: row.session ?? row.agentId,
    status: row.status,
    harness: row.harness ?? '—',
    model: modelBasename(row.model),
  };
}

/** Sort comparator: by status rank, then agent id for a stable, deterministic order. */
function byStatusThenId(a: RosterRow, b: RosterRow): number {
  const rankA = STATUS_RANK[a.status] ?? STATUS_RANK_FALLBACK;
  const rankB = STATUS_RANK[b.status] ?? STATUS_RANK_FALLBACK;
  return rankA - rankB || a.agentId.localeCompare(b.agentId);
}

/**
 * The pure view-model transform — the testable core. Sorts a copy (never mutates the slice's
 * readonly array) and projects each row. Same input → same output, no React, no store, no bus.
 */
export function selectRosterView(state: RosterState): RosterView {
  const rows = [...state.rows].sort(byStatusThenId).map(toRowView);
  return {
    rows,
    status: state.status,
    error: state.error,
    isEmpty: rows.length === 0,
  };
}

/**
 * Component-facing hook: memoizes {@link selectRosterView} on the slice identity. Because the store
 * ref-swaps the whole `roster` slice only on change, `state` is referentially stable between
 * unrelated re-renders, so this re-sorts only when the roster actually changed. A component does:
 *   `const view = useRosterView(useAppStore((s) => s.roster));`
 */
export function useRosterView(state: RosterState): RosterView {
  return useMemo(() => selectRosterView(state), [state]);
}
