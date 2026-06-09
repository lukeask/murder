/**
 * ReportsPanel — the reports list, panel 3 (ctrl+3).
 *
 * Copied from {@link ./NotesPanel.tsx} (including C11's star + open-doc extensions). Only these
 * differ: slice `s.reports`, `PANEL_ID` `'reports'`, doc kind `'report'`, empty chrome `'no
 * reports'`, keymap descriptions say "report". Everything else is verbatim.
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
import { useDocView } from './DocViewMode.js';

const PANEL_ID: PanelId = 'reports';
const PANEL_TITLE = 'Reports';

type ReportsIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

/**
 * One report entry rendered as a two-line block.
 * Line 1: star marker + name. Line 2: char count · updated time.
 * Memoised on row + cursor + starred.
 */
const ReportEntry = memo(function ReportEntry({
  row,
  selected,
  starred,
}: {
  readonly row: ReportRowView;
  readonly selected: boolean;
  readonly starred: boolean;
}): React.JSX.Element {
  const marker = selected ? '▌' : ' ';
  const star = starred ? '★ ' : '';
  return (
    <Box flexDirection="column">
      <Text inverse={selected} wrap="truncate">
        {`${marker} ${star}${row.name}`}
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
        <ReportEntry key={row.name} row={row} selected={index === cursor} starred={row.starred} />
      ))}
    </Box>
  );
}

/** The reports panel. Reads its slice, runs the selector, owns a local cursor, declares its
 * keymap, and paints a focus-highlighted bordered box of two-line entries. `React.memo`'d (rule 1). */
export const ReportsPanel = memo(function ReportsPanel(): React.JSX.Element {
  const reports = useAppStore((s) => s.reports, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const view = useReportsView(reports, favorites);
  const refresh = useAppStore((s) => s.actions.reports.refresh);
  const toggleFavorite = useAppStore((s) => s.actions.favorites.toggle);
  const toggleDoc = useDocView('report');

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

  const rowNameAtCursor = useCallback((): string | null => {
    const clamped = Math.min(cursor, Math.max(rowCount - 1, 0));
    return view.rows[clamped]?.name ?? null;
  }, [cursor, rowCount, view.rows]);

  const keymap: PanelKeymap<ReportsIntent> = useMemo(
    () => ({
      keymap: [
        { chord: { input: 'j' }, intent: 'cursorDown', description: 'next report' },
        { chord: { input: 'k' }, intent: 'cursorUp', description: 'prev report' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: { input: 's', key: { meta: true } }, intent: 'star', description: 'star' },
        { chord: { key: { return: true } }, intent: 'open', description: 'view doc' },
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
          case 'star': {
            const name = rowNameAtCursor();
            if (name !== null) {
              void toggleFavorite(name);
            }
            return;
          }
          case 'open': {
            const name = rowNameAtCursor();
            if (name !== null) {
              toggleDoc(name);
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [moveCursor, refresh, toggleFavorite, toggleDoc, rowNameAtCursor],
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
