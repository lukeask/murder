/**
 * Usage view-models — presentation for the Usage panel (panel 9, C9).
 *
 * Rule 2 in action: all formatting of pct, bar-width, and time-remaining labels lives here,
 * never in the component. The UsagePanel receives `UsageView` with pre-formatted strings.
 *
 * Two layers (mirrors rosterSelectors.ts):
 *  - **Pure transforms** (`selectUsageView`) — no React, unit-testable in isolation.
 *  - **A `useMemo` hook** (`useUsageView`) — component-facing, memoises on slice identity.
 */

import { useMemo } from 'react';
import type { UsageRow, UsageState } from '../store/usage/usageSlice.js';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Bar width in characters for the usage percentage bar. */
export const USAGE_BAR_WIDTH = 20;

/**
 * One usage gauge row as the UsagePanel paints it — all strings already formatted. The
 * component does zero arithmetic or string-building: that is this selector's job (rule 2).
 */
export interface UsageRowView {
  readonly harness: string;
  readonly windowKey: string;
  /** Formatted percentage string, e.g. `'73%'`. */
  readonly pctLabel: string;
  /** Visual bar string of `USAGE_BAR_WIDTH` chars, e.g. `'███████████████░░░░░'`. */
  readonly bar: string;
  /** Time until reset, formatted for display, e.g. `'4m'` or `'—'`. */
  readonly resetLabel: string;
  /** True when usage is at or above 80% — the component may highlight red. */
  readonly isHigh: boolean;
}

/** The whole usage view: rows in display order plus load-lifecycle flags. */
export interface UsageView {
  readonly rows: readonly UsageRowView[];
  readonly status: UsageState['status'];
  readonly error: string | null;
  readonly isEmpty: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a fixed-width block bar. Filled portion uses `'█'`, empty uses `'░'`. */
function buildBar(pct: number, width: number): string {
  const filled = Math.round(Math.min(Math.max(pct, 0), 100) * (width / 100));
  return '█'.repeat(filled) + '░'.repeat(width - filled);
}

/** Format minutes remaining as a human label. */
function formatMinutes(minutes: number): string {
  if (minutes <= 0) return '—';
  const m = Math.ceil(minutes);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem === 0 ? `${h}h` : `${h}h${rem}m`;
}

function toRowView(row: UsageRow): UsageRowView {
  return {
    harness: row.harness,
    windowKey: row.windowKey,
    pctLabel: `${Math.round(row.pct)}%`,
    bar: buildBar(row.pct, USAGE_BAR_WIDTH),
    resetLabel: formatMinutes(row.tUntilResetMinutes),
    isHigh: row.pct >= 80,
  };
}

/** Sort by percentage descending (most-used first), then harness name for stability. */
function byPctDesc(a: UsageRow, b: UsageRow): number {
  return b.pct - a.pct || a.harness.localeCompare(b.harness);
}

// ---------------------------------------------------------------------------
// Pure transform
// ---------------------------------------------------------------------------

/**
 * The pure view-model transform — the testable core. Sorts by usage desc and projects each
 * row to a display-ready tuple. Same input → same output; no React, no store, no bus.
 */
export function selectUsageView(state: UsageState): UsageView {
  const rows = [...state.rows].sort(byPctDesc).map(toRowView);
  return {
    rows,
    status: state.status,
    error: state.error,
    isEmpty: rows.length === 0,
  };
}

/**
 * Component-facing hook: memoises {@link selectUsageView} on the slice identity. Because the
 * store ref-swaps the whole `usage` slice only on change, `state` is referentially stable
 * between unrelated re-renders.
 *
 * Usage: `const view = useUsageView(useAppStore((s) => s.usage));`
 */
export function useUsageView(state: UsageState): UsageView {
  return useMemo(() => selectUsageView(state), [state]);
}
