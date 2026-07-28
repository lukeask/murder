/**
 * Workspace bar widget — a fixed-width tab strip of every configured workspace number
 * (e.g. ` 1  2  3  4 ` for four workspaces; three cells per tab). Scales with `count` (2–9);
 * collapses when only one workspace is configured.
 *
 * Inactive tabs are dim; the active tab fills with the focus accent (same green as the active
 * panel border) and inverse label text so it reads as a selected cell.
 */

import type { TextRun } from '../render/cellSurface.js';

/** Colors for the active tab fill (focus accent + text that reads on that fill). */
export interface WorkspaceBarColors {
  /** Background of the active tab — typically {@link Theme.focus}. */
  readonly activeBg: string;
  /** Foreground on the active fill — typically {@link Theme.gaugeLabelText}. */
  readonly activeFg: string;
}

/** One renderable workspace bar segment (styled runs + display width). */
export interface WorkspaceBarWidgetSegment {
  readonly runs: readonly TextRun[];
  readonly width: number;
}

/** Cells per workspace tab (`␠n␠`). */
const TAB_WIDTH = 3;

/**
 * Pure view-model for the workspace indicator. Returns `null` when `count == 1` (feature inert).
 * When `colors` is omitted the active tab is bold-only (no fill) — useful in structure-only tests.
 */
export function selectWorkspaceBarWidget(
  activeIndex: number,
  count: number,
  colors?: WorkspaceBarColors,
): WorkspaceBarWidgetSegment | null {
  if (count <= 1) {
    return null;
  }

  const runs: TextRun[] = [];
  for (let i = 0; i < count; i++) {
    const text = ` ${i + 1} `;
    if (i === activeIndex) {
      runs.push({
        text,
        style:
          colors === undefined
            ? { bold: true }
            : { bold: true, bg: colors.activeBg, fg: colors.activeFg },
      });
    } else {
      runs.push({ text, style: { dim: true } });
    }
  }
  return { runs, width: count * TAB_WIDTH };
}
