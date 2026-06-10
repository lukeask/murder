/**
 * `<Overlay>` — the render slot for the active transient mode (see {@link ../input/modeStore.js}).
 * Mounted once in {@link ./App.js App}, it reads the active mode and paints its surface in the
 * presentation the mode *declares* — `modal`, `fullscreen`, or `inlayout`. The three presentations
 * the modal-ish chunks depend on are expressed as **data** (the mode's `presentation` field), so a
 * consumer picks a variant rather than reinventing layout, and adding a fourth presentation is one
 * `case` here, not a change in every consumer.
 *
 * ## The three presentations (Box layout per variant)
 *
 *  - **`modal`** — a centered bordered box drawn *over* the panels. Ink has no true z-layer, so an
 *    overlay can't paint on top of an already-rendered tree; instead the shell renders *either* the
 *    layout *or* the modal-with-the-layout-context. We implement "over" as: a full-size container
 *    that centers the mode's box with flex alignment. The panels are not torn down (the shell keeps
 *    them mounted so their stores/effects persist), but the modal occupies the foreground region.
 *    Centered via `justifyContent="center" alignItems="center"` over the terminal's full width/height.
 *  - **`fullscreen`** — the mode's surface *replaces* the whole layout: a full width/height container
 *    holding only the mode's render. The shell suppresses its own bars/panels for this variant (see
 *    {@link ./App.js}), so a full-screen takeover (C14's tmux frame) owns the screen.
 *  - **`inlayout`** — the surface occupies a layout region while the panels stay visible. The overlay
 *    contributes no positioning of its own here: it renders the mode's component inline and lets the
 *    *mode's own render* place itself within the region the shell gives it (C8's in-layout editor
 *    sits beside the focused panel). This is the "no `$EDITOR`-blank" path — surrounding panels stay.
 *
 * ## Why a thin slot
 *
 * The Overlay owns *only* the per-presentation outer Box; the mode's `render` owns the surface
 * content. That split keeps the primitive narrow: a consumer writes a normal component and declares a
 * presentation; it never positions a modal or computes terminal size. `fullscreen` vs `modal` vs
 * `inlayout` is the only layout decision, and it lives here, once.
 *
 * Rendering nothing when no mode is up means the slot is zero-cost in the common case.
 */

import { Box, useStdout } from 'ink';
import type { ReactNode } from 'react';
import { useInputStores, useModeStore } from '../hooks/useInputStores.js';
import { type ModePresentation, selectActiveMode } from '../input/modeStore.js';

/** A reasonable floor so a modal/fullscreen still lays out before the first `useStdout` measurement
 * (some non-TTY renders report no size). Ink clamps to the real terminal once known. */
const FALLBACK_COLUMNS = 80;
const FALLBACK_ROWS = 24;

/**
 * Whether the shell should hide its own chrome (bars + panels) for the active mode. A `fullscreen`
 * mode takes the whole screen, so the shell renders only the overlay; `modal`/`inlayout` keep the
 * layout. Exported so {@link ./App.js} reads the same predicate the overlay lays out against — the
 * suppression decision lives with the presentation data, not duplicated in the shell.
 */
export function presentationHidesLayout(presentation: ModePresentation): boolean {
  return presentation === 'fullscreen';
}

/** The active mode's component wrapped in the Box layout its presentation declares. */
function PresentedMode({
  presentation,
  children,
}: {
  readonly presentation: ModePresentation;
  readonly children: ReactNode;
}): ReactNode {
  const { stdout } = useStdout();
  const columns = stdout?.columns ?? FALLBACK_COLUMNS;
  const rows = stdout?.rows ?? FALLBACK_ROWS;

  switch (presentation) {
    case 'modal':
      // Centered over the full terminal: the mode's box floats in the middle; its own render decides
      // its size/border. The container fills the screen so centering is against the whole viewport.
      return (
        <Box width={columns} height={rows} justifyContent="center" alignItems="center">
          {children}
        </Box>
      );
    case 'fullscreen':
      // The surface replaces the layout: a full-viewport container holding only the mode.
      return (
        <Box width={columns} height={rows} flexDirection="column">
          {children}
        </Box>
      );
    case 'inlayout':
      // No outer positioning — the mode renders inline within the region the shell hands it, so the
      // surrounding panels stay visible. The mode's own render owns its width/height in the region.
      return children;
    default:
      return presentation satisfies never;
  }
}

/**
 * The overlay render slot. Reads the active mode; renders nothing when none is up. Otherwise wraps
 * the mode's declared `render()` in the Box layout for its presentation. The mode's render is a thin
 * component (a popup body, an editor, an ANSI frame) — the Overlay supplies only the framing.
 */
export function Overlay(): ReactNode {
  const { modes } = useInputStores();
  // Subscribe to the stack so a push/pop re-renders; the rendered value is the derived active mode
  // (selectActiveMode reads the same stack handle), keeping the derivation in the one place — mirrors
  // how focus reads go through selectEffectiveFocus.
  useModeStore((s) => s.stack);
  const active = selectActiveMode(modes);
  if (active === null) {
    return null;
  }
  return <PresentedMode presentation={active.presentation}>{active.render()}</PresentedMode>;
}
