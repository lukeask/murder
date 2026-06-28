import { type JSX, memo, useCallback, useEffect, useMemo } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../../hooks/useAppStore.js';
import { useBindings, usePanelKeymap } from '../../hooks/useInputStores.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import { usePlansView } from '../../selectors/plansSelectors.js';
import { useTheme } from '../../theme/themeStore.js';
import { useDocView } from './docView.js';
import { listSurfaceStatus } from './listSurfaceStatus.js';
import { PlansSurface } from './PlansSurface.js';
import { MeasuredPaneFrame, useClampedCursor } from './shared/index.js';

type PlansIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open' | 'spawnPlanner';

export interface PlansControllerProps {
  readonly presentation: PanePresentation;
}

export const PlansController = memo(function PlansController({
  presentation,
}: PlansControllerProps): JSX.Element {
  const plans = useAppStore((state) => state.plans, shallow);
  const favorites = useAppStore((state) => state.favorites, shallow);
  const refresh = useAppStore((state) => state.actions.plans.refresh);
  const toggleFavorite = useAppStore((state) => state.actions.favorites.toggle);
  const spawnPlanner = useAppStore((state) => state.actions.plans.spawnPlanner);
  const bindings = useBindings();
  const toggleDoc = useDocView('plan');
  const view = usePlansView(plans, favorites);
  const theme = useTheme();
  const { cursor, moveDown, moveUp } = useClampedCursor(view.rows.length);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const rowIdAtCursor = useCallback(
    (): string | null => view.rows[cursor]?.id ?? null,
    [cursor, view.rows],
  );

  const keymap: PanelKeymap<PlansIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next plan',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev plan',
        },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: bindings.chordsFor('panel.star'), intent: 'star', description: 'favorite' },
        { chord: { key: { return: true } }, intent: 'open', description: 'view doc' },
        { chord: { input: 'p' }, intent: 'spawnPlanner', description: 'spawn planner' },
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
            const id = rowIdAtCursor();
            if (id !== null) {
              void toggleFavorite(id);
            }
            return;
          }
          case 'open': {
            const id = rowIdAtCursor();
            if (id !== null) {
              toggleDoc(id);
            }
            return;
          }
          case 'spawnPlanner': {
            const id = rowIdAtCursor();
            if (id !== null) {
              void spawnPlanner(id);
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [bindings, moveDown, moveUp, refresh, rowIdAtCursor, spawnPlanner, toggleDoc, toggleFavorite],
  );
  usePanelKeymap('plans', keymap);

  return (
    <MeasuredPaneFrame id="plans" presentation={presentation}>
      <PlansSurface
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        theme={theme}
        rows={view.rows}
        cursor={cursor}
        status={listSurfaceStatus(view.status)}
        error={view.error}
      />
    </MeasuredPaneFrame>
  );
});
