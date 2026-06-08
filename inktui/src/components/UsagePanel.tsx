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

const PANEL_ID: PanelId = 'usage';
const PANEL_TITLE = 'Usage';

type UsageIntent = 'cursorDown' | 'cursorUp' | 'refresh';

/**
 * One usage gauge row: harness label, block bar, percentage, and reset countdown.
 * Highlighted when usage is high (≥80%). Memoised on row + selected.
 */
const UsageEntry = memo(function UsageEntry({
  row,
  selected,
}: {
  readonly row: UsageRowView;
  readonly selected: boolean;
}): React.JSX.Element {
  const marker = selected ? '▌' : ' ';
  const barColor = row.isHigh ? 'red' : 'green';
  return (
    <Text inverse={selected} wrap="truncate">
      {`${marker} `}
      <Text bold>{row.harness.padEnd(8)}</Text>
      {'  '}
      <Text color={barColor}>{row.bar}</Text>
      {'  '}
      <Text color={row.isHigh ? 'red' : 'white'}>{row.pctLabel.padStart(4)}</Text>
      {'  '}
      <Text dimColor>{row.resetLabel}</Text>
    </Text>
  );
});

/** The list body: loading/error/empty chrome, else one entry per gauge. */
function UsageList({
  view,
  cursor,
}: {
  readonly view: UsageView;
  readonly cursor: number;
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
    <Box flexDirection="column">
      {view.rows.map((row, index) => (
        <UsageEntry key={`${row.harness}-${row.windowKey}`} row={row} selected={index === cursor} />
      ))}
    </Box>
  );
}

/**
 * The usage panel. Reads the usage slice, runs the selector to a display-ready view, owns a
 * local cursor, declares its keymap, and paints a focus-highlighted bordered box.
 * `React.memo`'d (rule 1) so it re-renders only when its own state changes.
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
      <UsageList view={view} cursor={Math.min(cursor, Math.max(rowCount - 1, 0))} />
    </Box>
  );
});
