/**
 * Crow roster width → display density (TUI {@link CrowsSurface} `layout` parity for meta).
 * Pixel thresholds approximate the TUI char budgets once chrome (avatar, trail, padding) is paid.
 */

export type CrowDisplayMode = 'full' | 'compact' | 'minimal';

/** Width (px) at/above which harness + model meta is shown. */
export const CROW_FULL_MIN_WIDTH = 280;
/** Width (px) at/above which model-only meta is shown (below → minimal / no meta). */
export const CROW_COMPACT_MIN_WIDTH = 200;

/** Map measured panel width to a density mode. Unmeasured (≤0) stays full. */
export function crowDensityFromWidth(widthPx: number): CrowDisplayMode {
  if (widthPx <= 0) return 'full';
  if (widthPx < CROW_COMPACT_MIN_WIDTH) return 'minimal';
  if (widthPx < CROW_FULL_MIN_WIDTH) return 'compact';
  return 'full';
}

/**
 * Whether the harness/model meta line should render.
 * `expanded` is the `m` toggle (false forces one-line rows); minimal width also hides meta.
 */
export function crowShowMeta(mode: CrowDisplayMode, expanded: boolean): boolean {
  if (!expanded) return false;
  return mode === 'full' || mode === 'compact';
}
