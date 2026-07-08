/**
 * Workspace bar widget — which workspace is active, e.g. `⟨2/3⟩`. Collapses when only one
 * workspace is configured.
 */

import type { TextRun } from '../render/cellSurface.js';

/** One renderable workspace bar segment (styled runs + display width). */
export interface WorkspaceBarWidgetSegment {
  readonly runs: readonly TextRun[];
  readonly width: number;
}

/**
 * Pure view-model for the workspace indicator. Returns `null` when `count == 1` (feature inert).
 */
export function selectWorkspaceBarWidget(
  activeIndex: number,
  count: number,
): WorkspaceBarWidgetSegment | null {
  if (count <= 1) {
    return null;
  }

  const body = `${activeIndex + 1}/${count}`;
  const runs: TextRun[] = [
    { text: '⟨', style: { dim: true } },
    { text: body, style: {} },
    { text: '⟩', style: { dim: true } },
  ];
  const width = runs.reduce((sum, run) => sum + run.text.length, 0);
  return { runs, width };
}
