/**
 * Rail — the orientation-aware side region that arranges a set of `Pane`s. Replaces the inline
 * `PanelRegion` that App.tsx used for the left and right regions (a fixed `flexDirection="row"`).
 *
 * One `<Rail>` serves BOTH sides (`side="left"|"right"`) and BOTH orientations; the only thing that
 * changes between them is the `flexDirection` the panels stack along:
 *
 *   landscape (Body is a `row`):  Rail is a vertical column → its panels STACK top-to-bottom,
 *                                 splitting the Rail's available *height* evenly (each panel
 *                                 `flexGrow={1}`).
 *   portrait  (Body is a `column`): Rail is a horizontal strip → its panels sit SIDE-BY-SIDE,
 *                                 splitting the Rail's available *width* evenly.
 *
 * (Note the Rail's flexDirection is the OPPOSITE of the Body's: a landscape Body lays Rails out in a
 * row, and each Rail stacks its panels in a column; a portrait Body stacks Rails in a column, and
 * each Rail lays its panels out in a row. App.tsx owns the Body direction; Rail owns its own.)
 *
 * ## Visibility (unchanged from PanelRegion)
 * The Rail reads the panel store's visible set itself (like the old `PanelRegion`) and renders, in
 * the given screen order, only the panels currently toggled on. When NONE are visible it returns
 * `null` so the region collapses out of the layout entirely — the spec's "left visible iff any of
 * 1–4 on, right visible iff usage/crows on". The caller passes the region's ordered panel ids; Rail
 * does the filtering so App.tsx need not re-derive the visible subset.
 *
 * ## Borders / double-border note (Phase 2/3 handoff)
 * In Phase 2 only PlansPanel is a `Pane`; the other five panels still draw their own
 * `<Box borderStyle>` chrome. Rail does NOT add a border of its own — it only arranges whatever panel
 * nodes `renderPanel` returns — so there is no double-border. When Phase 3 converts the remaining
 * panels to `Pane`, this file does not change: it already just lays out the nodes it's handed.
 *
 * ## Clipping discipline (kept from PanelRegion)
 * Each panel wrapper and the Rail itself keep `minHeight={0}` + `overflow="hidden"` so a panel taller
 * than its bounded share clips instead of growing the frame past the terminal height (which breaks
 * Ink's in-place redraw — see {@link ../hooks/useTerminalSize.js}).
 *
 * Presentational glue (rules 1/5): reads only the panel store's visible set, renders via the injected
 * `renderPanel` dispatch, no `useInput`, no bus.
 */

import { Box } from 'ink';
import type { JSX } from 'react';
import { usePanelStore } from '../hooks/useInputStores.js';
import type { Orientation } from '../hooks/useOrientation.js';
import type { PanelId } from '../input/panels.js';

export interface RailProps {
  /** Which side this Rail is (left = panels 1–4, right = usage/crows). Currently only documents
   * intent + keys the region; the layout is symmetric, so it is not yet branched on. */
  readonly side: 'left' | 'right';
  /** Live layout orientation (threaded from the one `useOrientation()` call in the Shell). */
  readonly orientation: Orientation;
  /** This region's panel ids in screen order; Rail filters to the visible subset, in this order. */
  readonly panels: readonly PanelId[];
  /** The {@link PanelId} → component dispatch (App.tsx's `renderPanel`), injected so Rail stays a
   * pure arranger and doesn't import the panel components itself. */
  readonly renderPanel: (id: PanelId) => JSX.Element;
}

/**
 * Arrange this side's visible panels. Returns `null` when the region has no visible panels (collapse
 * out of the layout). landscape → `column` (panels stack, split height); portrait → `row` (panels
 * side-by-side, split width). The cross-axis gap (`rowGap`/`columnGap`) gives a one-cell breather
 * between stacked/adjacent panels, matching the old region's `columnGap={1}`.
 */
export function Rail({ side, orientation, panels, renderPanel }: RailProps): JSX.Element | null {
  const visible = usePanelStore((s) => s.visible);
  const shown = panels.filter((id) => visible.has(id));
  if (shown.length === 0) {
    return null;
  }
  const flexDirection = orientation === 'landscape' ? 'column' : 'row';
  return (
    <Box
      key={side}
      flexDirection={flexDirection}
      rowGap={orientation === 'landscape' ? 1 : 0}
      columnGap={orientation === 'portrait' ? 1 : 0}
      flexGrow={1}
      minHeight={0}
      overflow="hidden"
    >
      {shown.map((id) => (
        <Box key={id} flexGrow={1} minHeight={0} overflow="hidden">
          {renderPanel(id)}
        </Box>
      ))}
    </Box>
  );
}
