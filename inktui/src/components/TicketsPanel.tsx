/**
 * TicketsPanel — the tickets list, panel 4 (ctrl+4).
 *
 * Copied from {@link ./NotesPanel.tsx} per the C5 copy recipe. The key difference from notes/reports
 * is the **2-row × 5-column layout** with **alternating background color every 2 terminal lines**
 * (every ticket occupies exactly 2 lines). The component does layout/color; the selector does ALL
 * formatting (rule 2 — the C7 risk: with multiple columns it's tempting to format inline here).
 *
 * Changes vs. NotesPanel:
 *  - Slice: `s.tickets` (via `useTicketsView`).
 *  - `PANEL_ID`: `'tickets'`.
 *  - Row layout: 5 `flexDirection="column"` boxes side-by-side, each with 2 `<Text>` lines:
 *      col 1: `idCell` / `titleCell`
 *      col 2: `statusCell` / `lastUpdateCell`
 *      col 3: `depsCell` / `scheduleCell`
 *      col 4: `harnessCell` / `modelCell`
 *      col 5: `planCell` / `worktreeCell`
 *  - Alternating background: `row.rowParity` (0 or 1) from the selector drives `backgroundColor`
 *    (`'#2a2a2a'` on odd parity, `undefined` on even) — every 2 terminal lines alternate subtly.
 *  - `row.depsSatisfied` drives deps cell color; no string-matching in the component (rule 2 proof).
 *  - Empty chrome: `'no tickets'`.
 *  - Row key: `id` (tickets keyed by ticket id).
 *  - Intents: `'cursorDown' | 'cursorUp' | 'refresh'` (C8 will add `'open'` for enter-to-edit).
 *
 * Everything else — `React.memo`, `useAppStore` narrow selector, `usePanelKeymap`, `useMemo`
 * keymap, `useFocusRef`, `useEffectiveFocus`, `useMeasureFocus` — is verbatim from the reference
 * panel. These are the framework glue that every panel gets for free by copying.
 *
 * **C8 handoff note:** The local cursor identifies the selected ticket by `view.rows[cursor]?.id`.
 * C8 should add an `'open'` intent to this panel's keymap (bound to `enter`) that pushes the
 * selected ticket id to the editor. The `TicketRowView` fields are all display-ready strings;
 * C8's editor needs the raw `TicketRow` from the slice (via the store), not the view-model strings.
 * The editor will call `ticket.get_detail { ticket_id }` for the body/frontmatter separately.
 */

import { Box, Text } from 'ink';
import { memo, useCallback, useMemo, useState } from 'react';
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

const PANEL_ID: PanelId = 'tickets';
const PANEL_TITLE = 'Tickets';

type TicketsIntent = 'cursorDown' | 'cursorUp' | 'refresh';

/**
 * One ticket entry rendered as a **2-row × 5-column** block with alternating background.
 *
 * 5 `flexDirection="column"` boxes side by side (each 2 terminal lines):
 *   col 1: `idCell` / `titleCell`
 *   col 2: `statusCell` / `lastUpdateCell`
 *   col 3: `depsCell` / `scheduleCell`
 *   col 4: `harnessCell` / `modelCell`
 *   col 5: `planCell` / `worktreeCell`
 *
 * `rowParity` (from the selector) drives the alternating background so every other 2-line ticket
 * block gets a subtle shade — "alternating color every 2 lines" (spec). The component receives
 * display-ready strings and the `depsSatisfied` boolean from the selector; no formatting here,
 * no string-matching on sentinel values (rule 2 proof).
 *
 * Memoised on row + cursor flag so only the entries whose selected-ness changes repaint.
 */
const TicketEntry = memo(function TicketEntry({
  row,
  selected,
}: {
  readonly row: TicketRowView;
  readonly selected: boolean;
}): React.JSX.Element {
  const marker = selected ? '▌' : ' ';
  // Alternating background: odd-parity rows get a slightly different shade so every other ticket
  // block stands apart visually. The parity comes from the selector (rule 2 — no index arithmetic
  // here). Selection (inverse) overrides the alternating shade.
  const altBg = row.rowParity === 1 && !selected ? '#1e1e2e' : undefined;
  return (
    <Box flexDirection="row" backgroundColor={altBg}>
      {/* Cursor marker — spans both lines by being in the outer row box */}
      <Text inverse={selected}>{marker} </Text>
      {/* col 1: id / title */}
      <Box flexDirection="column" marginRight={2}>
        <Text bold={selected} inverse={selected}>
          {row.idCell}
        </Text>
        <Text dimColor={!selected}>{row.titleCell}</Text>
      </Box>
      {/* col 2: status / last-update */}
      <Box flexDirection="column" marginRight={2}>
        {selected ? (
          <Text inverse>{row.statusCell}</Text>
        ) : (
          <Text color="cyan">{row.statusCell}</Text>
        )}
        <Text dimColor={!selected}>{row.lastUpdateCell}</Text>
      </Box>
      {/* col 3: deps / schedule */}
      <Box flexDirection="column" marginRight={2}>
        {selected ? (
          <Text inverse>{row.depsCell}</Text>
        ) : row.depsSatisfied ? (
          <Text color="green">{row.depsCell}</Text>
        ) : (
          <Text color="yellow">{row.depsCell}</Text>
        )}
        <Text dimColor={!selected}>{row.scheduleCell}</Text>
      </Box>
      {/* col 4: harness / model */}
      <Box flexDirection="column" marginRight={2}>
        <Text bold={selected} inverse={selected}>
          {row.harnessCell}
        </Text>
        <Text dimColor={!selected}>{row.modelCell}</Text>
      </Box>
      {/* col 5: plan / worktree (CONTRACT GAP — both '—' until B13) */}
      <Box flexDirection="column">
        <Text dimColor={!selected} inverse={selected}>
          {row.planCell}
        </Text>
        <Text dimColor={!selected}>{row.worktreeCell}</Text>
      </Box>
    </Box>
  );
});

/** The list body: empty/loading/error chrome, else the two-line entries with alternating color. */
function TicketsList({
  view,
  cursor,
}: {
  readonly view: TicketsView;
  readonly cursor: number;
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
    <Box flexDirection="column">
      {view.rows.map((row, index) => (
        <TicketEntry key={row.id} row={row} selected={index === cursor} />
      ))}
    </Box>
  );
}

/**
 * The tickets panel. Reads its slice, runs the selector, owns a local cursor, declares its keymap,
 * and paints a focus-highlighted bordered box of 2-row ticket entries with alternating color.
 * `React.memo`'d (rule 1) so it re-renders only when the tickets slice changes or focus changes.
 *
 * **Rule 2 proof:** this component contains ZERO formatting logic. Every string in `TicketEntry`
 * (`idCell`, `titleCell`, `statusCell`, `lastUpdateCell`, `depsCell`, `depsSatisfied`,
 * `scheduleCell`, `harnessCell`, `modelCell`, `planCell`, `worktreeCell`, `rowParity`) comes from
 * {@link useTicketsView}. The component only places them in Boxes and wires layout/color/focus.
 * Notably: deps color uses `depsSatisfied` (a boolean from the selector), not string-matching on
 * `depsCell === 'ok'` — this is the C7 rule-2 hardening the advisor recommended.
 */
export const TicketsPanel = memo(function TicketsPanel(): React.JSX.Element {
  // Rule 1: read exactly this slice (shallow), rule 2: selector produces the view-model.
  const tickets = useAppStore((s) => s.tickets, shallow);
  const view = useTicketsView(tickets);
  // Rule 3: bus reached only through the dispatched action.
  const refresh = useAppStore((s) => s.actions.tickets.refresh);

  // Rule 1: cursor is local UI state.
  const [cursor, setCursor] = useState(0);
  const rowCount = view.rows.length;

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
  // C8 will add an 'open' intent bound to 'return' (enter) for the editor handoff.
  const keymap: PanelKeymap<TicketsIntent> = useMemo(
    () => ({
      keymap: [
        { chord: { input: 'j' }, intent: 'cursorDown', description: 'next ticket' },
        { chord: { input: 'k' }, intent: 'cursorUp', description: 'prev ticket' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
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
          default:
            return intent satisfies never;
        }
      },
    }),
    [moveCursor, refresh],
  );
  usePanelKeymap(PANEL_ID, keymap);

  // Focus highlight + rect registration — identical across every panel (rule 5).
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === PANEL_ID;
  useMeasureFocus(PANEL_ID, ref);

  return (
    <Box
      ref={ref}
      flexDirection="column"
      borderStyle="round"
      borderColor={focused ? 'green' : 'gray'}
      paddingX={1}
      flexGrow={1}
    >
      <Text bold color={focused ? 'green' : 'white'}>
        {PANEL_TITLE}
      </Text>
      <TicketsList view={view} cursor={Math.min(cursor, Math.max(rowCount - 1, 0))} />
    </Box>
  );
});
