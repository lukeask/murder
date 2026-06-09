/**
 * UsagePanel — the usage right panel (panel 9, C9).
 *
 * Copied from {@link RosterPanel.tsx} per the C5 copy recipe. Changes vs. RosterPanel:
 *  - Slice: `useAppStore((s) => s.usage, shallow)`.
 *  - Selector: `useUsageView` (formats pct, bar, reset-label; rule 2: zero formatting here).
 *  - `PANEL_ID`: `'usage'` (already in PanelId; no panels.ts edit needed).
 *  - Layout: one line per gauge (harness + bar + pct + reset time). Narrow single-line format
 *    suitable for the right region where `usage` sits left of `crows`.
 *
 * ## Phase 3: Pane + Ledger conversion
 * Converted to the layout primitives following {@link ./PlansPanel.tsx}. The bordered chrome is now
 * a {@link ./Pane.tsx Pane} and the gauge list is a {@link ./Ledger.tsx Ledger} with
 * `linesPerEntry=1` (single column). The cursor `useState`, keymap, selector usage, and focus
 * wiring are unchanged. The Ledger owns the full-width highlight + alternating background, so
 * `renderEntry` does NOT set `inverse`; it uses `ctx.selected` only for the `▌` marker. The
 * per-segment colors (bar/pct) come from the selector's `isHigh` flag (rule 2 — no formatting here).
 *
 * The panel is `React.memo`'d (rule 1) and reaches the bus only through the dispatched
 * `actions.usage.refresh` action (rule 3).
 */

import { Box, Text } from 'ink';
import { memo, useMemo, useState } from 'react';
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
import { type UsageRowView, type UsageView, useUsageView } from '../selectors/usageSelectors.js';
import { Ledger, type LedgerEntryContext } from './Ledger.js';
import { Pane } from './Pane.js';

const PANEL_ID: PanelId = 'usage';
const PANEL_TITLE = 'Usage';

/**
 * Fixed Ledger budget until the Pane measures and passes down its inner content size (see the
 * matching TODO + handoff note in {@link ./PlansPanel.tsx} / {@link ./Ledger.tsx}).
 */
const LEDGER_HEIGHT = 40;
const LEDGER_WIDTH = 40;

type UsageIntent = 'cursorDown' | 'cursorUp' | 'refresh';

/**
 * Render one usage gauge as a single-line Ledger entry: harness label, block bar, percentage, reset
 * countdown. The Ledger owns the full-width highlight + alt-bg, so this only uses `ctx.selected` for
 * the `▌` marker (no `inverse`). Single column (`maxColumns=1`). Per-segment colors come from the
 * selector's `isHigh` flag (rule 2).
 */
function renderUsageEntry(row: UsageRowView, ctx: LedgerEntryContext): React.ReactNode {
  const marker = ctx.selected ? '▌' : ' ';
  const barColor = row.isHigh ? 'red' : 'green';
  return (
    // Single-line entry, but still a `column` Box so the Ledger's full-width background spans it
    // (rule (b) from the PlansPanel reference). `flexShrink={0}` so Yoga doesn't drop the line.
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Text wrap="truncate">
        {`${marker} `}
        <Text bold>{row.harness.padEnd(8)}</Text>
        {'  '}
        <Text color={barColor}>{row.bar}</Text>
        {'  '}
        <Text color={row.isHigh ? 'red' : 'white'}>{row.pctLabel.padStart(4)}</Text>
        {'  '}
        <Text dimColor>{row.resetLabel}</Text>
      </Text>
    </Box>
  );
}

/** The list body: loading/error/empty chrome (Ledger renders nothing for zero rows), else one
 * single-line entry per gauge via {@link Ledger}. */
function UsageList({
  view,
  cursor,
  focused,
}: {
  readonly view: UsageView;
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
    return <Text dimColor>no usage data</Text>;
  }
  return (
    <Ledger
      rows={view.rows}
      cursor={cursor}
      focused={focused}
      linesPerEntry={1}
      minColumns={1}
      maxColumns={1}
      availableHeight={LEDGER_HEIGHT}
      availableWidth={LEDGER_WIDTH}
      renderEntry={renderUsageEntry}
      rowKey={(row) => `${row.harness}-${row.windowKey}`}
    />
  );
}

/**
 * The usage panel. Reads the usage slice, runs the selector to a display-ready view, owns a
 * local cursor, declares its keymap, and paints a focus-highlighted Pane of single-line Ledger
 * entries. `React.memo`'d (rule 1) so it re-renders only when its own state changes.
 */
export const UsagePanel = memo(function UsagePanel(): React.JSX.Element {
  // Rule 1: narrow selector (shallow).
  // Rule 2: selector produces display-ready rows; no formatting here.
  const usage = useAppStore((s) => s.usage, shallow);
  const view = useUsageView(usage);
  // Rule 3: bus reached only through the dispatched action.
  const refresh = useAppStore((s) => s.actions.usage.refresh);

  // Local UI state: cursor (rule 1).
  const [cursor, setCursor] = useState(0);
  const rowCount = view.rows.length;

  // Rule 5: keymap as data in useMemo.
  const keymap: PanelKeymap<UsageIntent> = useMemo(
    () => ({
      keymap: [
        { chord: { input: 'j' }, intent: 'cursorDown', description: 'next gauge' },
        { chord: { input: 'k' }, intent: 'cursorUp', description: 'prev gauge' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            setCursor((c) => (rowCount === 0 ? 0 : Math.min(c + 1, rowCount - 1)));
            return;
          case 'cursorUp':
            setCursor((c) => Math.max(c - 1, 0));
            return;
          case 'refresh':
            void refresh();
            return;
          default:
            return intent satisfies never;
        }
      },
    }),
    [rowCount, refresh],
  );
  usePanelKeymap(PANEL_ID, keymap);

  // Focus highlight + rect registration — identical across every panel (rule 5).
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === PANEL_ID;
  useMeasureFocus(PANEL_ID, ref);

  return (
    <Pane ref={ref} title={PANEL_TITLE} focused={focused}>
      <UsageList
        view={view}
        cursor={Math.min(cursor, Math.max(rowCount - 1, 0))}
        focused={focused}
      />
    </Pane>
  );
});
