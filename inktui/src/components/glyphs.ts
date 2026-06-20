/**
 * glyphs — the unified triangle family shared across the TUI chrome.
 *
 * One source of truth so the scroll-overflow indicators drawn on the {@link ./paneBorder.tsx
 * paneBorder} (`▴ N` / `▾ N`) and the {@link ./ChatInput.tsx ChatInput} send-target arrow (`▸`)
 * all use the same triangle weight — a hairline-matched small-solid set, not a grab-bag of
 * arrows/triangles of mismatched stroke. Swap the active line below for the heavier CP437/WGL4
 * variant if a terminal/font renders the small solids poorly.
 */

// export const TRI_UP = '▲', TRI_DOWN = '▼', TRI_RIGHT = '►'; // U+25B2/25BC/25BA — heavier, CP437/WGL4-universal
// biome-ignore format: the family stays on ONE line so it lines up with the commented heavier alternative above (swap-in-place).
export const TRI_UP = '▴', TRI_DOWN = '▾', TRI_RIGHT = '▸', TRI_LEFT = '◂'; // U+25B4/25BE/25B8/25C2 — small solid, hairline-matched

/** The harness ◇ model separator glyph worn on a chat pane's bottom border (a hollow diamond, kin to
 * the Git Tree panel's filled ◆ node but lighter so it reads as a divider, not a marker). */
export const META_SEP = '◇'; // U+25C7

/** Ink `borderStyle` values used for pane chrome — round (blurred) vs bold (focused/highlighted). */
export type PaneInkBorderStyle = 'round' | 'bold';

/** Glyphs for the hand-composed pane border segments (top row, scroll track, footer overlay). */
export interface PaneBorderGlyphs {
  /** Leading corner + horizontal run on the top border (`╭─ ` / `┏━ `). */
  readonly topLeftPrefix: string;
  readonly topRight: string;
  readonly horizontal: string;
  readonly vertical: string;
  readonly bottomLeft: string;
  readonly bottomRight: string;
}

/** Round (light) and bold (heavy) pane border sets — mirrors Ink's `cli-boxes` `round` / `bold`. */
export const PANE_BORDER_GLYPHS: Record<PaneInkBorderStyle, PaneBorderGlyphs> = {
  round: {
    topLeftPrefix: '╭─ ',
    topRight: '╮',
    horizontal: '─',
    vertical: '│',
    bottomLeft: '╰',
    bottomRight: '╯',
  },
  bold: {
    topLeftPrefix: '┏━ ',
    topRight: '┓',
    horizontal: '━',
    vertical: '┃',
    bottomLeft: '┗',
    bottomRight: '┛',
  },
};

/** Full-block glyph for the scrollbar thumb on the right-border track. */
export const SCROLL_THUMB = '\u2588';

/** Focus/highlight → heavy border; blurred → round. Shared by Pane, ChatInput, RosterPanel. */
export function paneBorderStyle(focused: boolean): PaneInkBorderStyle {
  return focused ? 'bold' : 'round';
}
