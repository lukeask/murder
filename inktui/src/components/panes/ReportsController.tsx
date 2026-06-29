import { type JSX, memo, useCallback, useEffect, useMemo } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../../hooks/useAppStore.js';
import { useBindings, usePanelKeymap } from '../../hooks/useInputStores.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import { useReportsView } from '../../selectors/reportsSelectors.js';
import { useTheme } from '../../theme/themeStore.js';
import { useDocView } from './docView.js';
import { ReportsSurface } from './ReportsSurface.js';
import { MeasuredPaneFrame } from './shared/MeasuredPaneFrame.js';
import { useClampedCursor } from './shared/useClampedCursor.js';

type ReportsIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

export interface ReportsControllerProps {
  readonly presentation: PanePresentation;
}

export const ReportsController = memo(function ReportsController({
  presentation,
}: ReportsControllerProps): JSX.Element {
  const reports = useAppStore((state) => state.reports, shallow);
  const favorites = useAppStore((state) => state.favorites, shallow);
  const refresh = useAppStore((state) => state.actions.reports.refresh);
  const toggleFavorite = useAppStore((state) => state.actions.favorites.toggle);
  const bindings = useBindings();
  const toggleDoc = useDocView('report');
  const view = useReportsView(reports, favorites);
  const theme = useTheme();
  const { cursor, moveDown, moveUp } = useClampedCursor(view.rows.length);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const rowNameAtCursor = useCallback(
    (): string | null => view.rows[cursor]?.name ?? null,
    [cursor, view.rows],
  );

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
            moveDown();
            return;
          case 'cursorUp':
            moveUp();
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
    [bindings, moveDown, moveUp, refresh, rowNameAtCursor, toggleDoc, toggleFavorite],
  );
  usePanelKeymap('reports', keymap);

  return (
    <MeasuredPaneFrame id="reports" presentation={presentation}>
      <ReportsSurface
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        theme={theme}
        rows={view.rows}
        cursor={cursor}
        status={view.status}
        error={view.error}
      />
    </MeasuredPaneFrame>
  );
});
