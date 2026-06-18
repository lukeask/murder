/**
 * Theme → CSS custom properties bridge.
 *
 * The user's #1 requirement is that styling be as easy as possible to change later. So ALL visual
 * styling lives in plain `.css` files (under `src/styles/`) driven by CSS custom properties — no
 * CSS-in-JS, no inline thematic style objects. This module is the ONE place the semantic
 * {@link Theme} (from `@core/theme`, the same role set the Ink UI uses) is projected onto those
 * variables. A future styling tweak is then "edit the CSS" or "add a palette role + one line here".
 *
 * Each semantic role becomes a `--color-<role>` (kebab-cased) custom property. Components reference
 * them in CSS (`color: var(--color-text)`), never the hex values directly.
 */

import type { Theme } from '@core/theme/buildTheme.js';

/** Map a built {@link Theme} to the `{ '--color-...': hex }` record written onto `:root`. The key
 * naming is mechanical: `rowSelectedBg` → `--color-row-selected-bg`. Kept in one pure function so
 * it is trivially unit-testable and the CSS contract is discoverable.
 *
 * Most roles are a single hex string. A role can also be an array of hexes (e.g. `laneColors`, the
 * Git Tree's per-branch lane palette). Array roles are projected two ways so CSS can pick either:
 * a comma-joined list var (`--color-lane-colors: #a, #b, …`, usable with no extra plumbing) plus an
 * indexed var per entry (`--color-lane-colors-0`, `--color-lane-colors-1`, …) for direct nth access.
 */
export function themeToCssVars(theme: Theme): Record<string, string> {
  const vars: Record<string, string> = {};
  for (const [role, value] of Object.entries(theme)) {
    const base = `--color-${camelToKebab(role)}`;
    if (typeof value === 'string') {
      vars[base] = value;
    } else {
      // An array role (e.g. laneColors): a comma-joined list var + one indexed var per entry.
      vars[base] = value.join(', ');
      value.forEach((entry, i) => {
        vars[`${base}-${i}`] = entry;
      });
    }
  }
  return vars;
}

/** Write the theme's CSS variables onto a root element (default `document.documentElement`). Used
 * by {@link useThemeCssVars}; exported so a test can target a detached element. */
export function applyThemeCssVars(theme: Theme, root?: HTMLElement): void {
  const target = root ?? document.documentElement;
  for (const [name, value] of Object.entries(themeToCssVars(theme))) {
    target.style.setProperty(name, value);
  }
}

/** `rowSelectedBg` → `row-selected-bg`. Lower-cases each capital and prefixes a hyphen. */
function camelToKebab(name: string): string {
  return name.replace(/[A-Z]/g, (ch) => `-${ch.toLowerCase()}`);
}
