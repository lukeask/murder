/**
 * theme — the single source of UI color for the Ink frontend.
 *
 * Two layers, on purpose:
 *  1. {@link palette} — the RAW color values of a named scheme (currently Everforest Dark, hard
 *     variant). Pure hex; no opinion about *where* a color is used. Swapping schemes = swapping this.
 *  2. {@link theme} — SEMANTIC roles ("the focused border", "a cursor-row highlight", "an error")
 *     that map onto palette entries. Components reference ONLY these roles, never raw hex or Ink's
 *     built-in color names. So a component never says `color="green"`; it says `color={theme.focus}`.
 *
 * Why the split: it makes a future **settings menu** a drop-in. To let users pick a scheme we only
 * need to (a) add more palettes and (b) make `theme` a value chosen at runtime instead of a const —
 * e.g. promote it to a Zustand slice + a `useTheme()` hook, or recompute `buildTheme(palette)` from
 * the user's pick. Because every component already goes through the semantic roles, that swap is
 * mechanical and touches no component. Until then `theme` is a plain const (no store dependency, no
 * re-render cost) and the whole UI is recolored by editing this one file.
 *
 * Ink accepts either a named color (`"green"`) or a hex string (`"#a7c080"`); we always hand it hex
 * from the palette so the look is identical across terminals (named colors honor the terminal's own
 * 16-color palette, which we explicitly do NOT want — the scheme should look the same everywhere).
 */

/**
 * Everforest Dark — hard background variant. Grouped as the upstream scheme documents it: tinted
 * background fills first (UI surfaces), then the saturated foreground accents (text + status), then
 * the greys. Values are the canonical Everforest hex; do not editorialize them here — add a new
 * palette object instead and point {@link theme} at it.
 */
export const palette = {
  // Background surfaces (darkest → lightest), plus the tinted "visual"/status backgrounds.
  bgDim: '#1e2326',
  bg0: '#272e33',
  bg1: '#2e383c',
  bg2: '#374145',
  bg3: '#414b50',
  bg4: '#495156',
  bg5: '#4f5b58',
  bgVisual: '#4c3743',
  bgRed: '#493b40',
  bgGreen: '#3c4841',
  bgBlue: '#384b55',
  bgYellow: '#45443c',
  // Foreground + saturated accents.
  fg: '#d3c6aa',
  red: '#e67e80',
  orange: '#e69875',
  yellow: '#dbbc7f',
  green: '#a7c080',
  aqua: '#83c092',
  blue: '#7fbbb3',
  purple: '#d699b6',
  // Greys (dim → bright).
  grey0: '#7a8478',
  grey1: '#859289',
  grey2: '#9da9a0',
} as const;

/**
 * Semantic UI roles — what components import. Each role names a JOB, not a color, so re-theming is a
 * matter of repointing the role at a different palette entry. Keep this list small and intentional;
 * if you reach for a raw palette color in a component, add a role here instead.
 */
export const theme = {
  // ── Branding ────────────────────────────────────────────────────────────────────────────────
  /** The `murder` wordmark (top-left). The one deliberately loud accent. */
  brand: palette.red,

  // ── Text ────────────────────────────────────────────────────────────────────────────────────
  /** Default body text. */
  text: palette.fg,
  /** De-emphasized text used where Ink's `dimColor` isn't enough (project name, inactive labels). */
  muted: palette.grey1,

  // ── Panel chrome / focus ─────────────────────────────────────────────────────────────────────
  /** Border + title of the pane that holds focus. */
  focus: palette.green,
  /** Border of a blurred pane. */
  borderBlurred: palette.grey0,
  /** Title of a blurred pane (still readable, just not the focus accent). */
  titleBlurred: palette.fg,

  // ── Lists (Ledger rows) ──────────────────────────────────────────────────────────────────────
  /** Full-width cursor-row highlight — a muted green band that reads as "selected" without glaring. */
  rowSelectedBg: palette.bgGreen,
  /** Subtle alternating-row shade (one step lighter than the base surface). */
  rowAltBg: palette.bg1,

  // ── Usage panel surfaces ─────────────────────────────────────────────────────────────────────
  /** Group-header band in the usage panel. */
  panelHeaderBg: palette.bg0,
  /** Selected-row band in the usage panel (non-Ledger, so it carries its own selection color). */
  panelSelectedBg: palette.bg2,

  // ── Status / semantic accents ────────────────────────────────────────────────────────────────
  /** Errors and destructive/over-limit states. */
  error: palette.red,
  /** Warnings, pending/blocked states, "needs attention". */
  warning: palette.yellow,
  /** Success, valid input, "ready". */
  success: palette.green,
  /** Headings/labels inside modals and editors (was the scattered `cyan`). */
  heading: palette.aqua,
  /** Secondary accent for emphasis that isn't a heading (was the scattered `blue`). */
  accent: palette.blue,

  // ── Active/inactive toggles (top-bar panel labels, mode indicators) ──────────────────────────
  /** An active/on toggle (lit panel label, insert mode, satisfied deps). */
  active: palette.green,
  /** An inactive/off toggle. */
  inactive: palette.grey0,

  // ── Gauges (usage bars) ──────────────────────────────────────────────────────────────────────
  /** A gauge within normal range. */
  gaugeNormal: palette.green,
  /** A gauge at/over its high-water mark. */
  gaugeHigh: palette.red,
} as const;

export type Theme = typeof theme;
