/**
 * Usage view-models — presentation for the Usage panel (panel 9, C9).
 *
 * Rule 2 in action: all formatting (pct label, period label, reset-countdown label, bar geometry)
 * lives here, never in the component. The UsagePanel receives a {@link UsageView} of display-ready
 * groups and does zero arithmetic or string-building. The ONE thing the selector deliberately does
 * NOT do is pick colors — `isHigh` is a flag and the bar is emitted as *geometry* (a filled-cell
 * count), so the component paints the segments (green/red/grey). That mirrors the old split where
 * the component chose `barColor` from `isHigh`.
 *
 * ## Grouping by provider (harness)
 * Usage now renders 2–3 lines per provider — a header line then one gauge line per rate-limit window
 * (e.g. codex → `5h` + `weekly`). So the view-model is GROUPED: `groups[]`, each a harness plus its
 * gauge windows, in first-seen order (the service already emits providers in `_PROVIDER_ORDER`, so
 * first-seen preserves that order). No global pct sort — grouping wins over ranking.
 *
 * Two layers (mirrors rosterSelectors.ts):
 *  - **Pure transform** (`selectUsageView`) — no React, unit-testable in isolation.
 *  - **A `useMemo` hook** (`useUsageView`) — component-facing, memoises on slice identity.
 */

import { useMemo } from 'react';
import type { UsageRow, UsageState } from '../store/usage/usageSlice.js';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** Bar width in characters for the usage gauge bar. Kept narrow now that providers are grouped and
 * the right rail is a thin column (the labels carry the precise numbers). */
export const USAGE_BAR_WIDTH = 12;

/**
 * One usage gauge (a single rate-limit window) as the UsagePanel paints it. Labels are pre-formatted
 * strings; the bar is emitted as *geometry* (`filledCount` over `barWidth`) so the component owns the
 * per-segment colors (rule 2 — the selector never picks a color).
 */
export interface UsageGaugeView {
  readonly windowKey: string;
  /** Formatted percentage string, e.g. `'73%'` — painted INSIDE the bar by the component. */
  readonly pctLabel: string;
  /** Window-length label, e.g. `'5h'`, `'7d'`, `'30d'`. `''` when unknown. */
  readonly periodLabel: string;
  /** Time until reset, formatted for display, e.g. `'1h52m'` or `'—'`. */
  readonly resetLabel: string;
  /** Total bar width in cells (= {@link USAGE_BAR_WIDTH}); the component rescales to its live width. */
  readonly barWidth: number;
  /** How many leading cells are filled (`█`); the rest are empty (`░`). */
  readonly filledCount: number;
  /** True when usage is at or above 80% — the component paints the fill red. */
  readonly isHigh: boolean;
}

/** One provider's block: the harness header plus its gauge windows, in wire order. */
export interface UsageGroupView {
  readonly harness: string;
  readonly gauges: readonly UsageGaugeView[];
}

/** The whole usage view: provider groups in display order plus load-lifecycle flags. */
export interface UsageView {
  readonly groups: readonly UsageGroupView[];
  readonly status: UsageState['status'];
  readonly error: string | null;
  readonly isEmpty: boolean;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MINUTES_PER_HOUR = 60;
const MINUTES_PER_DAY = 24 * 60;

/** Number of leading filled cells for `pct` over `width`. */
function filledCells(pct: number, width: number): number {
  return Math.round((Math.min(Math.max(pct, 0), 100) / 100) * width);
}

/** Format a window length as a coarse `Xd`/`Xh`/`Xm` label (days when a whole multiple of a day). */
function formatPeriod(minutes: number): string {
  if (minutes <= 0) return '';
  if (minutes % MINUTES_PER_DAY === 0) return `${minutes / MINUTES_PER_DAY}d`;
  if (minutes % MINUTES_PER_HOUR === 0) return `${minutes / MINUTES_PER_HOUR}h`;
  return `${Math.round(minutes)}m`;
}

/** Format minutes-until-reset as a human label (`Xm` / `Xh` / `XhYm`); `—` when none. */
function formatMinutes(minutes: number): string {
  if (minutes <= 0) return '—';
  const m = Math.ceil(minutes);
  if (m < MINUTES_PER_HOUR) return `${m}m`;
  const h = Math.floor(m / MINUTES_PER_HOUR);
  const rem = m % MINUTES_PER_HOUR;
  return rem === 0 ? `${h}h` : `${h}h${rem}m`;
}

function toGaugeView(row: UsageRow): UsageGaugeView {
  return {
    windowKey: row.windowKey,
    pctLabel: `${Math.round(row.pct)}%`,
    periodLabel: formatPeriod(row.tPeriodMinutes),
    resetLabel: formatMinutes(row.tUntilResetMinutes),
    barWidth: USAGE_BAR_WIDTH,
    filledCount: filledCells(row.pct, USAGE_BAR_WIDTH),
    isHigh: row.pct >= 80,
  };
}

// ---------------------------------------------------------------------------
// Pure transform
// ---------------------------------------------------------------------------

/**
 * The pure view-model transform — the testable core. Groups rows by harness in first-seen order (no
 * global sort: grouping replaces ranking) and projects each window to a display-ready gauge. Same
 * input → same output; no React, no store, no bus.
 */
export function selectUsageView(state: UsageState): UsageView {
  const groups: UsageGroupView[] = [];
  const byHarness = new Map<string, UsageGaugeView[]>();
  for (const row of state.rows) {
    let gauges = byHarness.get(row.harness);
    if (gauges === undefined) {
      gauges = [];
      byHarness.set(row.harness, gauges);
      groups.push({ harness: row.harness, gauges });
    }
    gauges.push(toGaugeView(row));
  }
  return {
    groups,
    status: state.status,
    error: state.error,
    isEmpty: groups.length === 0,
  };
}

/**
 * Component-facing hook: memoises {@link selectUsageView} on the slice identity. Because the store
 * ref-swaps the whole `usage` slice only on change, `state` is referentially stable between unrelated
 * re-renders.
 *
 * Usage: `const view = useUsageView(useAppStore((s) => s.usage));`
 */
export function useUsageView(state: UsageState): UsageView {
  return useMemo(() => selectUsageView(state), [state]);
}
