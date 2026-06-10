/**
 * palettes — the RAW color schemes the UI can wear.
 *
 * A {@link Palette} is the unopinionated layer: named slots (`bg0`, `green`, `grey1`, …) holding
 * pure hex, with no notion of *where* a color is used. The semantic-role mapping lives in
 * {@link ../theme/buildTheme.ts buildTheme} — components reference only roles, never these slots,
 * so adding a scheme is "add a palette here, register it below". Every palette MUST expose the
 * same slot keys (enforced by the {@link Palette} type) so {@link buildTheme} works over any of
 * them.
 *
 * Ink accepts a named color (`"green"`) or a hex string; we always feed hex so the look is identical
 * across terminals (named colors honor the terminal's own 16-color palette, which we do NOT want).
 */

/**
 * Everforest Dark — hard background variant. Grouped as upstream documents it: tinted background
 * fills first (UI surfaces), then saturated foreground accents (text + status), then the greys.
 * Canonical Everforest hex; do not editorialize — add a new palette object instead.
 */
export const everforestDarkHard = {
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
 * Everforest Light — hard background variant. Canonical upstream light-hard hex. Note the surface
 * ramp inverts (lightest = the "darkest" UI surface here): `bgDim`/`bg0` are the brightest paper
 * tones and the ramp *darkens* toward `bg5`, which keeps {@link buildTheme}'s "one step lighter"
 * intentions readable on a light background (see the per-palette role notes in buildTheme). The
 * `green`/`red`/etc. accents are the upstream light-variant values, which are darker than the dark
 * theme's so they keep contrast against the pale paper.
 */
export const everforestLightHard = {
  // Background surfaces (lightest paper → progressively darker UI bands), plus tinted status fills.
  bgDim: '#f2efdf',
  bg0: '#fffbef',
  bg1: '#f8f5e4',
  bg2: '#f2efdf',
  bg3: '#edeada',
  bg4: '#e8e5d5',
  bg5: '#bec5b2',
  bgVisual: '#f0f2d4',
  bgRed: '#fbe3da',
  bgGreen: '#f3f5d9',
  bgBlue: '#eaedf3',
  bgYellow: '#fbecd4',
  // Foreground + saturated accents (light-variant: darker, for contrast on the pale paper).
  fg: '#5c6a72',
  red: '#f85552',
  orange: '#f57d26',
  yellow: '#dfa000',
  green: '#8da101',
  aqua: '#35a77c',
  blue: '#3a94c5',
  purple: '#df69ba',
  // Greys (light-variant; the "dim → bright" intent reads as "lighter → darker" on paper).
  grey0: '#a6b0a0',
  grey1: '#939f91',
  grey2: '#829181',
} as const;

/**
 * The shape every palette satisfies. {@link buildTheme} is generic over this, so a new scheme only
 * has to fill in the same slots. Typed off the dark palette since both share identical keys.
 */
export type Palette = { readonly [K in keyof typeof everforestDarkHard]: string };

/** The set of selectable schemes, keyed by their stable id. */
export const PALETTES = {
  'everforest-dark': everforestDarkHard,
  'everforest-light': everforestLightHard,
} as const satisfies Record<string, Palette>;

/** A scheme id usable by the settings menu / persisted config. */
export type ThemeId = keyof typeof PALETTES;

/** Default scheme when nothing is persisted. */
export const DEFAULT_THEME_ID: ThemeId = 'everforest-dark';
