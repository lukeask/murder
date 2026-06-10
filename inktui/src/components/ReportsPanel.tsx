/**
 * ReportsPanel — the reports list, panel 3 (ctrl+3).
 *
 * Copied from {@link ./NotesPanel.tsx} (including C11's star + open-doc extensions). Only these
 * differ: slice `s.reports`, `PANEL_ID` `'reports'`, doc kind `'report'`, empty chrome `'no
 * reports'`, keymap descriptions say "report". Everything else is verbatim.
 *
 * ## Phase 3: Pane + Ledger conversion
 * Converted to the layout primitives following {@link ./PlansPanel.tsx} / {@link ./NotesPanel.tsx}.
 * The bordered chrome is now a {@link ./Pane.tsx Pane} and the two-line list is a
 * {@link ./Ledger.tsx Ledger} (single column, `linesPerEntry=2`). The cursor `useState`, keymap,
 * selector usage, and focus wiring are unchanged; only the rendering moved to the primitives.
 * `renderEntry` does NOT set `inverse` (Ledger owns the full-width highlight + alt-bg); it uses
 * `ctx.selected` only for the `▌` marker + line-2 dim.
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
import { theme } from '../theme.js';
import { useDocView } from './DocPane.js';
import { Ledger, type LedgerEntryContext } from './Ledger.js';
import { Pane } from './Pane.js';

const PANEL_ID: PanelId = 'reports';
const PANEL_TITLE = 'Reports';

// The Ledger self-measures its own inner size now (see {@link ./Ledger.tsx}'s "Sizing" note), so no
// fixed budget is passed: its overflow window tracks the live panel size.

type ReportsIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

/**
 * Render one report row as a two-line Ledger entry. Line 1: cursor marker + star + name. Line 2:
 * char count · updated time. Ledger owns the highlight + alt-bg, so this only uses `ctx.selected`
 * for the `▌` marker + line-2 dim (no `inverse`). Single column (`maxColumns=1`).
 */
function renderReportEntry(row: ReportRowView, ctx: LedgerEntryContext): React.ReactNode {
  const marker = ctx.selected ? '▌' : ' ';
  // FIXED-WIDTH star gutter (bug 2): `★ ` when starred, two spaces otherwise — name column is fixed.
  const star = row.starred ? '★ ' : '  ';
  return (
    // Leading gutter is marker(1)+star(2)=3; line-2's 3-space indent matches so `charCount` sits under `name`.
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Text wrap="truncate">{`${marker}${star}${row.name}`}</Text>
      <Text dimColor={!ctx.selected} wrap="truncate">
        {`   ${row.charCount} · ${row.updatedAt}`}
      </Text>
    </Box>
  );
}

/**
 * The Ledger column-titles key — a dim two-line block labeling the entry lines: `name` over
 * `size · updated`. The 3-space leading indent matches {@link renderReportEntry}'s gutter
 * (marker + star) so the labels sit directly above the data columns (bug 1).
 */
function renderReportsHeader(): React.ReactNode {
  return (
    <Box flexDirection="column" flexShrink={0}>
      <Text dimColor>{'   name'}</Text>
      <Text dimColor>{'   size · updated'}</Text>
    </Box>
  );
}

/** The list body: empty/loading/error chrome (Ledger renders nothing for zero rows), else the
 * two-line entries via {@link Ledger}. */
function ReportsList({
  view,
  cursor,
  focused,
}: {
  readonly view: ReportsView;
  readonly cursor: number;
  readonly focused: boolean;
}): React.JSX.Element {
  if (view.status === 'error') {
    return <Text color={theme.error}>{`error: ${view.error ?? 'unknown'}`}</Text>;
  }
  if (view.status === 'loading' && view.isEmpty) {
    return <Text dimColor>loading…</Text>;
  }
  if (view.isEmpty) {
    return <Text dimColor>no reports</Text>;
  }
  return (
    <Ledger
      rows={view.rows}
      cursor={cursor}
      focused={focused}
      linesPerEntry={2}
      minColumns={1}
      maxColumns={1}
      renderEntry={renderReportEntry}
      header={renderReportsHeader}
      rowKey={(row) => row.name}
    />
  );
}

/** The reports panel. Reads its slice, runs the selector, owns a local cursor, declares its
 * keymap, and paints a focus-highlighted Pane of two-line Ledger entries. `React.memo`'d (rule 1). */
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
        { chord: { input: 'f', key: { meta: true } }, intent: 'star', description: 'favorite' },
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
    <Pane ref={ref} title={PANEL_TITLE} focused={focused}>
      <ReportsList
        view={view}
        cursor={Math.min(cursor, Math.max(rowCount - 1, 0))}
        focused={focused}
      />
    </Pane>
  );
});
