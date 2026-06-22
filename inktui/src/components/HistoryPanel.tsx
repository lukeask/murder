/**
 * HistoryPanel — the user-intention history feed, panel 5 (ctrl+5).
 *
 * Copied from {@link ./NotesPanel.tsx} per the panel copy recipe. Differences:
 *  - Slice: `s.history` (via {@link useHistoryView}); rows are user intentions with a zero-LLM
 *    status (open/stale/dismissed) derived server-side.
 *  - `PANEL_ID`: `'history'`. ctrl+5 arrives free via the digit dispatcher — no new global chord.
 *  - Local state: a `cursor` (like every panel) AND a `mode` (`'loose' | 'all'`) the `a` key toggles
 *    — loose threads (OPEN+STALE, oldest first) by default, the full reverse-chron feed on toggle.
 *  - Keys: j/k or ↓/↑ cursor; `a` toggles loose↔all; `x` dismisses the row under the cursor.
 *  - Row layout: a two-line Ledger entry. Line 1 = age · target · STATUS tag. Line 2 = the intention
 *    text (truncated to fit). The header shows "N loose threads".
 *
 * Row counts are deterministic (the Ledger windows on `linesPerEntry`), so this never relies on
 * measureElement for wrapped text (the measure-wrap trap). `flexShrink: 0` on list rows.
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
  type HistoryMode,
  type HistoryRowView,
  type HistoryView,
  useHistoryView,
} from '../selectors/historySelectors.js';
import { useTheme } from '../theme/themeStore.js';
import { Ledger, type LedgerEntryContext } from './Ledger.js';
import { Pane } from './Pane.js';

const PANEL_ID: PanelId = 'history';

type HistoryIntent = 'cursorDown' | 'cursorUp' | 'resumeOrRefresh' | 'toggleMode' | 'dismiss';

/** Map a row status to a theme color for its tag. */
function statusColor(status: string, theme: ReturnType<typeof useTheme>): string {
  switch (status) {
    case 'stale':
      return theme.warning;
    case 'dismissed':
      return theme.muted;
    default:
      return theme.accent; // open
  }
}

/**
 * Render one history row as a two-line Ledger entry. Line 1: cursor marker + age · target · STATUS.
 * Line 2: the intention text. The Ledger paints the full-width selection background, so this only
 * uses `ctx.selected` for the `▌` marker + line-2 dim (it does NOT set `inverse`).
 */
function renderHistoryEntry(
  row: HistoryRowView,
  ctx: LedgerEntryContext,
  theme: ReturnType<typeof useTheme>,
): React.ReactNode {
  const marker = ctx.selected ? '▌' : ' ';
  return (
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Text wrap="truncate">
        {`${marker} ${row.age.padEnd(8)} ${row.target}  `}
        <Text color={statusColor(row.status, theme)}>{row.statusTag}</Text>
      </Text>
      <Text dimColor={!ctx.selected} wrap="truncate">
        {`    ${row.text}`}
      </Text>
    </Box>
  );
}

/** The Ledger column-titles key — a dim two-line block labeling the entry lines. */
function renderHistoryHeader(): React.ReactNode {
  return (
    <Box flexDirection="column" flexShrink={0}>
      <Text dimColor>{'  age      target  status'}</Text>
      <Text dimColor>{'    intention'}</Text>
    </Box>
  );
}

/** The list body: empty/loading/error chrome (Ledger renders nothing for zero rows), else the
 * two-line entries via {@link Ledger}. */
function HistoryList({
  view,
  mode,
  cursor,
  focused,
  onOverflow,
}: {
  readonly view: HistoryView;
  readonly mode: HistoryMode;
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
    return <Text dimColor>{mode === 'loose' ? 'no loose threads' : 'no history'}</Text>;
  }
  return (
    <Ledger
      rows={view.rows}
      cursor={cursor}
      focused={focused}
      linesPerEntry={2}
      minColumns={1}
      maxColumns={1}
      renderEntry={(row, ctx) => renderHistoryEntry(row, ctx, theme)}
      header={renderHistoryHeader}
      rowKey={(row) => row.itemId}
      onWindow={(win) => onOverflow({ above: win.start, below: view.rows.length - win.end })}
    />
  );
}

/** The history panel. Reads its slice, runs the selector, owns a local cursor + mode, declares its
 * keymap, and paints a focus-highlighted Pane of two-line Ledger entries. `React.memo`'d. */
export const HistoryPanel = memo(function HistoryPanel(): React.JSX.Element {
  const history = useAppStore((s) => s.history, shallow);
  const [mode, setMode] = useState<HistoryMode>('loose');
  const view = useHistoryView(history, mode);
  const refresh = useAppStore((s) => s.actions.history.refresh);
  const dismiss = useAppStore((s) => s.actions.history.dismiss);
  const resumeConversation = useAppStore((s) => s.actions.history.resumeConversation);

  const [cursor, setCursor] = useState(0);
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

  // Resolve the cursor row's item id at call time — the cursor is local, so the panel is the only
  // place that knows which row `x` (dismiss) acts on.
  const rowAtCursor = useCallback((): HistoryRowView | null => {
    const clamped = Math.min(cursor, Math.max(rowCount - 1, 0));
    return view.rows[clamped] ?? null;
  }, [cursor, rowCount, view.rows]);

  const keymap: PanelKeymap<HistoryIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next item',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev item',
        },
        { chord: { input: 'r' }, intent: 'resumeOrRefresh', description: 'resume / refresh' },
        { chord: { input: 'a' }, intent: 'toggleMode', description: 'loose ↔ all' },
        { chord: { input: 'x' }, intent: 'dismiss', description: 'dismiss' },
      ],
      onIntent(intent) {
        switch (intent) {
          case 'cursorDown':
            moveCursor(1);
            return;
          case 'cursorUp':
            moveCursor(-1);
            return;
          case 'resumeOrRefresh': {
            // `r` resumes the cursor row's CC session when resumable; on any other row it falls
            // through to a plain feed refresh (also the error-state "r to retry" affordance).
            const row = rowAtCursor();
            if (row?.resumable) {
              void resumeConversation(row.target);
              return;
            }
            void refresh();
            return;
          }
          case 'toggleMode':
            setMode((m) => (m === 'loose' ? 'all' : 'loose'));
            return;
          case 'dismiss': {
            const row = rowAtCursor();
            if (row !== null) {
              void dismiss(row.itemId);
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [moveCursor, refresh, dismiss, resumeConversation, rowAtCursor],
  );
  usePanelKeymap(PANEL_ID, keymap);

  const ref = useFocusRef();
  const focused = useEffectiveFocus() === PANEL_ID;
  useMeasureFocus(PANEL_ID, ref);

  // Title carries the loose-thread digest (the real hook) + the active mode.
  const title = `History · ${view.looseCount} loose${mode === 'all' ? ' · all' : ''}`;

  return (
    <Pane
      ref={ref}
      title={title}
      focused={focused}
      overflowAbove={rowCount === 0 ? 0 : overflow.above}
      overflowBelow={rowCount === 0 ? 0 : overflow.below}
    >
      <HistoryList
        view={view}
        mode={mode}
        cursor={Math.min(cursor, Math.max(rowCount - 1, 0))}
        focused={focused}
        onOverflow={setOverflow}
      />
    </Pane>
  );
});
