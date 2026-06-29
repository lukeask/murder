import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../../hooks/useAppStore.js';
import { usePanelKeymap } from '../../hooks/useInputStores.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import {
  type HistoryMode,
  type HistoryRowView,
  useHistoryView,
} from '../../selectors/historySelectors.js';
import { useTheme } from '../../theme/themeStore.js';
import { HistorySurface, type HistorySurfaceRow } from './HistorySurface.js';
import { MeasuredPaneFrame } from './shared/MeasuredPaneFrame.js';
import { useClampedCursor } from './shared/useClampedCursor.js';

type HistoryIntent = 'cursorDown' | 'cursorUp' | 'resumeOrRefresh' | 'toggleMode' | 'dismiss';

type SurfaceStatus = 'idle' | 'loading' | 'error';

function surfaceStatus(status: 'idle' | 'loading' | 'ready' | 'error'): SurfaceStatus {
  return status === 'loading' || status === 'error' ? status : 'idle';
}

export function historySurfaceRowsFromView(
  rows: readonly HistoryRowView[],
): readonly HistorySurfaceRow[] {
  return rows.map((row) => ({
    id: row.itemId,
    age: row.age,
    target: row.target,
    status: row.status,
    text: row.text,
  }));
}

export interface HistoryControllerProps {
  readonly presentation: PanePresentation;
}

export const HistoryController = memo(function HistoryController({
  presentation,
}: HistoryControllerProps): React.JSX.Element {
  const history = useAppStore((state) => state.history, shallow);
  const refresh = useAppStore((state) => state.actions.history.refresh);
  const dismiss = useAppStore((state) => state.actions.history.dismiss);
  const resumeConversation = useAppStore((state) => state.actions.history.resumeConversation);
  const [mode, setMode] = useState<HistoryMode>('loose');
  const view = useHistoryView(history, mode);
  const theme = useTheme();
  const rows = useMemo(() => historySurfaceRowsFromView(view.rows), [view.rows]);
  const { cursor, moveDown, moveUp } = useClampedCursor(rows.length);
  const cursorRef = useRef(cursor);
  const rowsRef = useRef(view.rows);
  cursorRef.current = cursor;
  rowsRef.current = view.rows;

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
            moveDown();
            return;
          case 'cursorUp':
            moveUp();
            return;
          case 'resumeOrRefresh': {
            const row = rowsRef.current[cursorRef.current];
            if (row?.resumable) {
              void resumeConversation(row.conversationId);
              return;
            }
            void refresh();
            return;
          }
          case 'toggleMode':
            setMode((current) => (current === 'loose' ? 'all' : 'loose'));
            return;
          case 'dismiss': {
            const row = rowsRef.current[cursorRef.current];
            if (row !== undefined) {
              void dismiss(row.itemId);
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [dismiss, moveDown, moveUp, refresh, resumeConversation],
  );
  usePanelKeymap('history', keymap);

  return (
    <MeasuredPaneFrame id="history" presentation={presentation}>
      <HistorySurface
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        theme={theme}
        rows={rows}
        mode={mode}
        cursor={cursor}
        status={surfaceStatus(view.status)}
        error={view.error}
      />
    </MeasuredPaneFrame>
  );
});
