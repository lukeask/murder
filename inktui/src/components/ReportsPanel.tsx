/**
 * ReportsPanel — the reports list, panel 3 (ctrl+3).
 *
 * Copied from {@link ./NotesPanel.tsx}. Only these differ:
 *  - Slice: `s.reports` (via `useReportsView`).
 *  - `PANEL_ID`: `'reports'`.
 *  - Empty chrome: `'no reports'`.
 *  - Row key and label still use `name` (same DTO shape as notes).
 *  - Keymap descriptions say "report" instead of "note".
 *
 * Everything else is verbatim — the framework glue every panel inherits by copying.
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
  type ReportRowView,
  type ReportsView,
  useReportsView,
} from '../selectors/reportsSelectors.js';

const PANEL_ID: PanelId = 'reports';
const PANEL_TITLE = 'Reports';

type ReportsIntent = 'cursorDown' | 'cursorUp' | 'refresh';

/**
 * One report entry rendered as a two-line block.
 * Line 1: name. Line 2: char count · updated time.
 * Memoised on row + cursor flag.
 */
const ReportEntry = memo(function ReportEntry({
  row,
  selected,
}: {
  readonly row: ReportRowView;
  readonly selected: boolean;
}): React.JSX.Element {
  const marker = selected ? '▌' : ' ';
  return (
    <Box flexDirection="column">
      <Text inverse={selected} wrap="truncate">
        {`${marker} ${row.name}`}
      </Text>
      <Text dimColor={!selected} inverse={selected} wrap="truncate">
        {`  ${row.charCount} · ${row.updatedAt}`}
      </Text>
    </Box>
  );
});

/** The list body: empty/loading/error chrome, else the two-line entries. */
function ReportsList({
  view,
  cursor,
}: {
  readonly view: ReportsView;
  readonly cursor: number;
}): React.JSX.Element {
  if (view.status === 'error') {
    return <Text color="red">{`error: ${view.error ?? 'unknown'}`}</Text>;
  }
  if (view.status === 'loading' && view.isEmpty) {
    return <Text dimColor>loading…</Text>;
  }
  if (view.isEmpty) {
    return <Text dimColor>no reports</Text>;
  }
  return (
    <Box flexDirection="column">
      {view.rows.map((row, index) => (
        <ReportEntry key={row.name} row={row} selected={index === cursor} />
      ))}
    </Box>
  );
}

/** The reports panel. Reads its slice, runs the selector, owns a local cursor, declares its
 * keymap, and paints a focus-highlighted bordered box of two-line entries. `React.memo`'d (rule 1). */
export const ReportsPanel = memo(function ReportsPanel(): React.JSX.Element {
  const reports = useAppStore((s) => s.reports, shallow);
  const view = useReportsView(reports);
  const refresh = useAppStore((s) => s.actions.reports.refresh);

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

  const keymap: PanelKeymap<ReportsIntent> = useMemo(
    () => ({
      keymap: [
        { chord: { input: 'j' }, intent: 'cursorDown', description: 'next report' },
        { chord: { input: 'k' }, intent: 'cursorUp', description: 'prev report' },
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
      <ReportsList view={view} cursor={Math.min(cursor, Math.max(rowCount - 1, 0))} />
    </Box>
  );
});
