/**
 * TicketsPanel — the tickets list, panel 4 (ctrl+4).
 *
 * Copied from {@link ./NotesPanel.tsx} per the C5 copy recipe. The key difference from notes/reports
 * is the **2-row × 5-column layout**: every ticket occupies exactly 2 terminal lines and lays out 5
 * field-columns side-by-side. The component does layout/color; the selector does ALL formatting
 * (rule 2 — the C7 risk: with multiple columns it's tempting to format inline here).
 *
 * Changes vs. NotesPanel:
 *  - Slice: `s.tickets` (via `useTicketsView`).
 *  - `PANEL_ID`: `'tickets'`.
 *  - Row layout: marker + up to 5 `flexDirection="column"` boxes side-by-side, each with 2 lines:
 *      col 1: `idCell` / `titleCell`
 *      col 2: `statusCell` / `lastUpdateCell`
 *      col 3: `depsCell` / `scheduleCell`
 *      col 4: `harnessCell` / `modelCell`
 *      col 5: `planCell` / `worktreeCell`
 *  - `row.depsSatisfied` drives deps cell color; no string-matching in the component (rule 2 proof).
 *  - Empty chrome: `'no tickets'`.
 *  - Row key: `id` (tickets keyed by ticket id).
 *  - Intents: `'cursorDown' | 'cursorUp' | 'refresh' | 'open'`.
 *
 * ## Phase 3: Pane + Ledger conversion — THE multi-column panel
 * Converted to the layout primitives. The bordered chrome is now a {@link ./Pane.tsx Pane}; the
 * 2-line × 5-column list is a {@link ./Ledger.tsx Ledger} with `linesPerEntry=2`,
 * `minColumns=1`/`maxColumns=5`. This is where the Ledger's responsive columns earn their keep:
 *  - The Ledger computes the active field count from its width budget and passes it as `ctx.columns`.
 *    `renderEntry` renders the leftmost `ctx.columns` of the 5 cell-columns — narrow drops the
 *    right-most first (worktree/plan → harness/model → …), and col 1 (id/title) is always present.
 *  - The alternating background now comes from the Ledger (by absolute row index), so the panel's old
 *    `rowParity`/`altBg` logic is GONE here. The selector still exposes `rowParity` (other code/tests
 *    may depend on it); the component just no longer applies it — Ledger owns parity.
 *  - The Ledger owns the full-width selection highlight, so `renderEntry` does NOT set `inverse`; it
 *    uses `ctx.selected` only for the `▌` marker. The deps (`depsSatisfied`) + status colors per cell
 *    are preserved (they don't fight the highlight).
 * The cursor `useState`, keymap, selector usage, and focus wiring are unchanged.
 *
 * **Rule 2 proof:** this component contains ZERO formatting logic. Every string comes from
 * {@link useTicketsView}; the component only places cells in Boxes and wires layout/color/focus.
 * Notably: deps color uses `depsSatisfied` (a boolean from the selector), not string-matching.
 */

import { Box, Text } from 'ink';
import { memo, useCallback, useMemo, useRef, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../hooks/useAppStore.js';
import {
  useEffectiveFocus,
  useFocusRef,
  useMeasureFocus,
  usePanelKeymap,
} from '../hooks/useInputStores.js';
import type { PanelKeymap } from '../input/keymap.js';
import type { PanelId } from '../input/panels.js';
import {
  type TicketRowView,
  type TicketsView,
  useTicketsView,
} from '../selectors/ticketsSelectors.js';
import { Ledger, type LedgerEntryContext } from './Ledger.js';
import { Pane } from './Pane.js';
import { useTicketEditor } from './TicketEditorMode.js';

const PANEL_ID: PanelId = 'tickets';
const PANEL_TITLE = 'Tickets';

// The Ledger self-measures its own inner size now (see {@link ./Ledger.tsx}'s "Sizing" note), so no
// fixed budget is passed: its overflow window AND its column-collapse (maxColumns=5 → fewer when the
// measured width is narrow) both track the live panel size. A wide tickets pane shows all 5 columns;
// a narrow one degrades gracefully right-to-left.

type TicketsIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'open';

/**
 * Render one ticket as a **2-row × up-to-5-column** Ledger entry. The cursor marker is the first
 * child (spanning both lines via the outer row Box); then the leftmost `ctx.columns` of the 5 cell
 * columns are rendered (col 1 always present; right-most drop first when narrow). The Ledger owns
 * the highlight + alternating background, so this sets NO `inverse`/`altBg` — only `ctx.selected`
 * for the `▌` marker. Deps color uses `depsSatisfied` (selector boolean), never string-matching.
 */
function renderTicketEntry(row: TicketRowView, ctx: LedgerEntryContext): React.ReactNode {
  const marker = ctx.selected ? '▌' : ' ';
  const cols = ctx.columns;
  return (
    // Rule (b) for a MULTI-column entry: this is a `row` (not a `column`) so the 5 cell-columns lay
    // out side-by-side; `flexGrow={1}` lets the Ledger's full-width background span it, `flexShrink={0}`
    // so Yoga doesn't drop a line.
    <Box flexDirection="row" flexGrow={1} flexShrink={0}>
      {/* Cursor marker — spans both lines by being in the outer row box */}
      <Text>{marker} </Text>
      {/* col 1: id / title (always present — minColumns=1) */}
      <Box flexDirection="column" marginRight={2}>
        <Text bold={ctx.selected}>{row.idCell}</Text>
        <Text dimColor={!ctx.selected}>{row.titleCell}</Text>
      </Box>
      {/* col 2: status / last-update */}
      {cols >= 2 ? (
        <Box flexDirection="column" marginRight={2}>
          <Text color="cyan">{row.statusCell}</Text>
          <Text dimColor={!ctx.selected}>{row.lastUpdateCell}</Text>
        </Box>
      ) : null}
      {/* col 3: deps / schedule — deps color from `depsSatisfied` (rule 2 proof) */}
      {cols >= 3 ? (
        <Box flexDirection="column" marginRight={2}>
          <Text color={row.depsSatisfied ? 'green' : 'yellow'}>{row.depsCell}</Text>
          <Text dimColor={!ctx.selected}>{row.scheduleCell}</Text>
        </Box>
      ) : null}
      {/* col 4: harness / model */}
      {cols >= 4 ? (
        <Box flexDirection="column" marginRight={2}>
          <Text bold={ctx.selected}>{row.harnessCell}</Text>
          <Text dimColor={!ctx.selected}>{row.modelCell}</Text>
        </Box>
      ) : null}
      {/* col 5: plan / worktree (CONTRACT GAP — both '—' until B13) */}
      {cols >= 5 ? (
        <Box flexDirection="column">
          <Text dimColor={!ctx.selected}>{row.planCell}</Text>
          <Text dimColor={!ctx.selected}>{row.worktreeCell}</Text>
        </Box>
      ) : null}
    </Box>
  );
}

/** The list body: empty/loading/error chrome (Ledger renders nothing for zero rows), else the
 * two-line × multi-column entries via {@link Ledger}. */
function TicketsList({
  view,
  cursor,
  focused,
}: {
  readonly view: TicketsView;
  readonly cursor: number;
  readonly focused: boolean;
}): React.JSX.Element {
  if (view.status === 'error') {
    return <Text color="red">{`error: ${view.error ?? 'unknown'}`}</Text>;
  }
  if (view.status === 'loading' && view.isEmpty) {
    return <Text dimColor>loading…</Text>;
  }
  if (view.isEmpty) {
    return <Text dimColor>no tickets</Text>;
  }
  return (
    <Ledger
      rows={view.rows}
      cursor={cursor}
      focused={focused}
      linesPerEntry={2}
      minColumns={1}
      maxColumns={5}
      renderEntry={renderTicketEntry}
      rowKey={(row) => row.id}
    />
  );
}

/**
 * The tickets panel. Reads its slice, runs the selector, owns a local cursor, declares its keymap,
 * and paints a focus-highlighted Pane of 2-row ticket entries. `React.memo`'d (rule 1) so it
 * re-renders only when the tickets slice changes or focus changes.
 */
export const TicketsPanel = memo(function TicketsPanel(): React.JSX.Element {
  // Rule 1: read exactly this slice (shallow), rule 2: selector produces the view-model.
  const tickets = useAppStore((s) => s.tickets, shallow);
  const view = useTicketsView(tickets);
  // Rule 3: bus reached only through the dispatched action.
  const refresh = useAppStore((s) => s.actions.tickets.refresh);

  // C8: editor lifecycle — openEditor(ticketId) enters the inlayout editor mode.
  const openEditor = useTicketEditor();

  // Rule 1: cursor is local UI state.
  const [cursor, setCursor] = useState(0);
  const rowCount = view.rows.length;
  // Stable ref so the keymap useMemo doesn't churn on every cursor change.
  const cursorRef = useRef(cursor);
  cursorRef.current = cursor;
  const rowsRef = useRef(view.rows);
  rowsRef.current = view.rows;

  const moveCursor = useCallback(
    (delta: number) => {
      setCursor((current) => {
        if (rowCount === 0) {
          return 0;
        }
        const next = current + delta;
        return Math.min(Math.max(next, 0), rowCount - 1);
      });
    },
    [rowCount],
  );

  // Rule 5: keymap as data, wrapped in useMemo so the registry effect doesn't churn.
  // `cursor` and `view.rows` are read via refs inside the intent handler so they stay
  // up-to-date without being in the deps array — the keymap object stays stable across
  // cursor moves and row updates, which keeps `usePanelKeymap`'s registration effect stable.
  const keymap: PanelKeymap<TicketsIntent> = useMemo(
    () => ({
      keymap: [
        { chord: { input: 'j' }, intent: 'cursorDown', description: 'next ticket' },
        { chord: { input: 'k' }, intent: 'cursorUp', description: 'prev ticket' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: { key: { return: true } }, intent: 'open', description: 'open ticket' },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            moveCursor(1);
            return;
          case 'cursorUp':
            moveCursor(-1);
            return;
          case 'refresh':
            void refresh();
            return;
          case 'open': {
            // C8: open the in-layout editor for the highlighted ticket.
            // Read cursor + rows via refs so this closure doesn't go stale.
            const rows = rowsRef.current;
            const safeIndex = Math.min(cursorRef.current, Math.max(rows.length - 1, 0));
            const row = rows[safeIndex];
            if (row !== undefined) {
              openEditor(row.id);
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [moveCursor, refresh, openEditor],
  );
  usePanelKeymap(PANEL_ID, keymap);

  // Focus highlight + rect registration — identical across every panel (rule 5).
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === PANEL_ID;
  useMeasureFocus(PANEL_ID, ref);

  return (
    <Pane ref={ref} title={PANEL_TITLE} focused={focused}>
      <TicketsList
        view={view}
        cursor={Math.min(cursor, Math.max(rowCount - 1, 0))}
        focused={focused}
      />
    </Pane>
  );
});
