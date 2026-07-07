/**
 * Usage bar widget — the shortest remaining reset timer across selected harnesses, e.g.
 * `usage cc 5h 42m`. Collapses when no qualifying usage rows exist for the selection.
 */

import type { TextRun } from '../render/cellSurface.js';
import type { UsageRow } from '../store/usage/usageSlice.js';
import { harnessShortLabel } from './harnessDisplay.js';
import { formatMinutes } from './usageSelectors.js';

/** One renderable usage bar segment (styled runs + display width). */
export interface UsageBarWidgetSegment {
  readonly runs: readonly TextRun[];
  readonly width: number;
}

/** `harnesses` empty/omitted → all harnesses; otherwise only listed ids. */
export function usageBarHarnessFilter(
  selectedHarnesses: readonly string[] | undefined,
): ReadonlySet<string> | null {
  if (selectedHarnesses === undefined || selectedHarnesses.length === 0) {
    return null;
  }
  return new Set(selectedHarnesses);
}

function rowMatchesHarness(row: UsageRow, filter: ReadonlySet<string> | null): boolean {
  return filter === null || filter.has(row.harness);
}

/** Per-harness minimum reset minutes (positive values only). */
function minResetByHarness(
  rows: readonly UsageRow[],
  filter: ReadonlySet<string> | null,
): ReadonlyMap<string, number> {
  const out = new Map<string, number>();
  for (const row of rows) {
    if (!rowMatchesHarness(row, filter)) {
      continue;
    }
    if (row.tUntilResetMinutes <= 0) {
      continue;
    }
    const prev = out.get(row.harness);
    if (prev === undefined || row.tUntilResetMinutes < prev) {
      out.set(row.harness, row.tUntilResetMinutes);
    }
  }
  return out;
}

/**
 * Pure view-model for the usage bar widget. Returns `null` when there is no usage data for the
 * selected harnesses (or every row lacks a positive reset time).
 */
export function selectUsageBarWidget(
  rows: readonly UsageRow[],
  selectedHarnesses: readonly string[] | undefined,
): UsageBarWidgetSegment | null {
  const byHarness = minResetByHarness(rows, usageBarHarnessFilter(selectedHarnesses));
  if (byHarness.size === 0) {
    return null;
  }

  let bestHarness = '';
  let bestMinutes = Number.POSITIVE_INFINITY;
  for (const [harness, minutes] of byHarness) {
    if (minutes < bestMinutes) {
      bestMinutes = minutes;
      bestHarness = harness;
    }
  }
  if (bestHarness === '' || !Number.isFinite(bestMinutes)) {
    return null;
  }

  const label = harnessShortLabel(bestHarness);
  const time = formatMinutes(bestMinutes);
  const body = `${label} ${time}`;
  const runs: TextRun[] = [
    { text: 'usage ', style: { dim: true } },
    { text: body, style: {} },
  ];
  const width = runs.reduce((sum, run) => sum + run.text.length, 0);
  return { runs, width };
}
