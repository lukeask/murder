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
 * {@link ./Ledger.tsx Ledger} (single column, `linesPerEntry=2`) over the shared
 * {@link ./ResourceRow.tsx} two-line row. The cursor `useState`, keymap, selector usage, and focus
 * wiring are unchanged; only the rendering moved to the primitives. `renderResourceEntry` does NOT
 * set `inverse` (Ledger owns the full-width highlight + alt-bg); it uses `ctx.selected` only for the
 * line-2 dim (and an optional cursor marker, disabled by default).
 */

import { Text } from 'ink';
import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../hooks/useAppStore.js';
import {
  useBindings,
  useEffectiveFocus,
  useFocusRef,
  useMeasureFocus,
  usePanelKeymap,
} from '../hooks/useInputStores.js';
import type { PanelKeymap } from '../input/keymap.js';
import type { PanelId } from '../input/panels.js';
import { type ReportsView, useReportsView } from '../selectors/reportsSelectors.js';
import { useTheme } from '../theme/themeStore.js';
import { useDocView } from './DocPane.js';
import { Ledger } from './Ledger.js';
import { Pane } from './Pane.js';
import { renderResourceEntry, renderResourceHeader } from './ResourceRow.js';

const PANEL_ID: PanelId = 'reports';
const PANEL_TITLE = 'Reports';

// The Ledger self-measures its own inner size now (see {@link ./Ledger.tsx}'s "Sizing" note), so no
// fixed budget is passed: its overflow window tracks the live panel size.

type ReportsIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

// The two-line row + header come from the shared {@link ./ResourceRow.tsx} renderer (plans/notes/
// reports paint the identical doc-style entry — flush-left, star shown only when starred, no forced
// cursor glyph). Reports' star-float sort lives in {@link ../selectors/reportsSelectors.js} and is
// baked into `row.starred` first. `ReportRowView` is structurally a `ResourceRowFields`.

/** The list body: empty/loading/error chrome (Ledger renders nothing for zero rows), else the
 * two-line entries via {@link Ledger}. */
function ReportsList({
  view,
  cursor,
  focused,
  onOverflow,
}: {
  readonly view: ReportsView;
  readonly cursor: number;
  readonly focused: boolean;
  readonly onOverflow: (o: { above: number; below: number }) => void;
}): React.JSX.Element {
  const theme = useTheme();
  if (view.status === 'error') {
    return <Text color={theme.error}>{`error: ${view.error ?? 'unknown'} (r to retry)`}</Text>;
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
      renderEntry={renderResourceEntry}
      header={renderResourceHeader}
      rowKey={(row) => row.name}
      onWindow={(win) => onOverflow({ above: win.start, below: view.rows.length - win.end })}
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

  // Fetch on first open. The Rail only mounts a panel while visible, so this runs when the user opens
  // Reports (ctrl+6) — the lazy fetch that replaces the (removed) eager startup prime. It moves the
  // slice off `idle`, so the gated invalidation entry in store.ts keeps it live thereafter. The
  // selector renders the empty/loading state until rows arrive. `refresh` is a stable store action.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const [cursor, setCursor] = useState(0);
  // Scroll-overflow counts fed up from the Ledger's window (via the list's onOverflow) into the Pane
  // border's ▴/▾ indicators. Reset to {0,0} when there are no rows (the Ledger doesn't render, so
  // onWindow never fires to clear a stale count) — see the rowCount===0 guard at the Pane below.
  const [overflow, setOverflow] = useState<{ above: number; below: number }>({
    above: 0,
    below: 0,
  });
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

  // The favorite/star chord comes from the central registry (`panel.star`); `bindings` is a stable
  // identity that changes only on a settings change, so it is a safe keymap dep (no churn).
  const bindings = useBindings();

  const keymap: PanelKeymap<ReportsIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next report',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev report',
        },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: bindings.chordsFor('panel.star'), intent: 'star', description: 'favorite' },
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
    [moveCursor, refresh, toggleFavorite, toggleDoc, rowNameAtCursor, bindings],
  );
  usePanelKeymap(PANEL_ID, keymap);

  const ref = useFocusRef();
  const focused = useEffectiveFocus() === PANEL_ID;
  useMeasureFocus(PANEL_ID, ref);

  return (
    <Pane
      ref={ref}
      title={PANEL_TITLE}
      focused={focused}
      overflowAbove={rowCount === 0 ? 0 : overflow.above}
      overflowBelow={rowCount === 0 ? 0 : overflow.below}
    >
      <ReportsList
        view={view}
        cursor={Math.min(cursor, Math.max(rowCount - 1, 0))}
        focused={focused}
        onOverflow={setOverflow}
      />
    </Pane>
  );
});
