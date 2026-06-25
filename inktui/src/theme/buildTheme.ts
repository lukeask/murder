/**
 * buildTheme — the SEMANTIC layer. Maps a raw {@link Palette} onto the UI roles components consume.
 *
 * Each role names a JOB ("the focused border", "a cursor-row highlight", "an error"), not a color.
 * Components import only these roles (via `useTheme()`), so re-theming is purely "swap the palette".
 * Keep this role list small and intentional; if a component reaches for a raw palette slot, add a
 * role here instead.
 *
 * Most roles map identically across palettes. A few SURFACE roles (selection bands, panel headers)
 * are palette-aware: on a dark scheme the tinted `bg*` slots read as muted bands, but on a light
 * scheme those same slots are nearly invisible against the pale paper, so we repoint them at slots
 * that keep the band visible. The semantic role is the contract — the slot it resolves to may
 * differ per palette.
 */

import type { Palette, ThemeVariant } from './palettes.js';

/**
 * Surface choices that must stay visible against the background. On dark schemes the tinted/low
 * `bg*` slots make subtle bands; on light schemes they vanish into the paper, so light schemes
 * point these at the *darker* end of the surface ramp instead. Defaults reproduce the dark
 * behavior exactly; light overrides keep the bands legible.
 */
interface SurfaceRoles {
  rowSelectedBg: string;
  rowAltBg: string;
  panelHeaderBg: string;
  panelSelectedBg: string;
}

function surfaceRoles(palette: Palette, variant: ThemeVariant): SurfaceRoles {
  if (variant === 'light') {
    // On paper the tinted `bg*` fills are near-white, so selection/header bands would disappear.
    // Repoint them onto the *darker* end of the light surface ramp (and the saturated `bgGreen`
    // for the selected row) so each band reads against the pale background while staying gentle.
    return {
      rowSelectedBg: palette.bgGreen, // tinted green wash — distinct against fffbef paper
      rowAltBg: palette.bg2, // one perceptible step down from the base paper
      panelHeaderBg: palette.bg4, // darker band so a header reads as a header
      panelSelectedBg: palette.bg5, // darkest surface band — clearly the selected row
    };
  }
  return {
    rowSelectedBg: palette.bgGreen,
    rowAltBg: palette.bg1,
    panelHeaderBg: palette.bg0,
    panelSelectedBg: palette.bg2,
  };
}

/** Build the semantic theme for a palette. Pure; safe to call from anywhere. */
export function buildTheme(palette: Palette, variant: ThemeVariant) {
  const surfaces = surfaceRoles(palette, variant);
  return {
    // ── Branding ──────────────────────────────────────────────────────────────────────────────
    /** The `murder` wordmark (top-left). The one deliberately loud accent. */
    brand: palette.red,

    // ── Text ──────────────────────────────────────────────────────────────────────────────────
    /** Default body text. */
    text: palette.fg,
    /** De-emphasized text used where Ink's `dimColor` isn't enough (project name, inactive labels). */
    muted: palette.grey1,

    // ── Panel chrome / focus ───────────────────────────────────────────────────────────────────
    /** Border + title of the pane that holds focus. */
    focus: palette.green,
    /** Border of a blurred pane. */
    borderBlurred: palette.grey0,
    /** Title of a blurred pane (still readable, just not the focus accent). */
    titleBlurred: palette.fg,

    // ── Lists (Ledger rows) ────────────────────────────────────────────────────────────────────
    /** Full-width cursor-row highlight — reads as "selected" without glaring. */
    rowSelectedBg: surfaces.rowSelectedBg,
    /** Subtle alternating-row shade (one step off the base surface). */
    rowAltBg: surfaces.rowAltBg,

    // ── Usage panel surfaces ───────────────────────────────────────────────────────────────────
    /** Group-header band in the usage panel. */
    panelHeaderBg: surfaces.panelHeaderBg,
    /** Selected-row band in the usage panel (non-Ledger, so it carries its own selection color). */
    panelSelectedBg: surfaces.panelSelectedBg,

    // ── Status / semantic accents ──────────────────────────────────────────────────────────────
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

    // ── Active/inactive toggles (top-bar panel labels, mode indicators) ────────────────────────
    /** An active/on toggle (lit panel label, insert mode, satisfied deps). */
    active: palette.green,
    /** An inactive/off toggle. */
    inactive: palette.grey0,

    // ── Gauges (usage bars) ────────────────────────────────────────────────────────────────────
    /** A gauge within normal range. */
    gaugeNormal: palette.green,
    /** A gauge at/over its high-water mark. */
    gaugeHigh: palette.red,
    /** The unused remainder of a gauge track (and the band behind a right-aligned pct label). */
    gaugeTrack: palette.grey1,
    /** Text painted ON a gauge band (the embedded pct label) — the base surface so it reads against
     * the saturated fill on dark schemes and the darker accents on light ones. */
    gaugeLabelText: palette.bg0,

    // ── Swimlane / DAG lanes (Git Tree panel) ──────────────────────────────────────────────────
    /** The per-branch accent ring the Git Tree panel cycles for NON-main lanes (one distinct color
     * per branch so every branch's railway + tag reads as its own color). `green` is deliberately
     * EXCLUDED — main owns it ({@link active}) and the selected-station glyph borrows {@link focus}
     * (also green), so keeping non-main lanes off green avoids a three-way clash. */
    laneColors: [
      palette.aqua,
      palette.purple,
      palette.orange,
      palette.yellow,
      palette.blue,
      palette.red,
    ] as readonly string[],
  } as const;
}

/**
 * The semantic theme type. Derived from a concrete `buildTheme` call so the role set is the single
 * source of truth (every palette produces the same shape).
 */
export type Theme = ReturnType<typeof buildTheme>;
