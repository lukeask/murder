import { type JSX, memo, useCallback, useEffect, useMemo } from 'react';
import { shallow } from 'zustand/shallow';
import { getPanelCreateActions } from '../../create/panelCreateActions.js';
import { useAppStore } from '../../hooks/useAppStore.js';
import { useBindings, usePanelKeymap } from '../../hooks/useInputStores.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import { usePlansView } from '../../selectors/plansSelectors.js';
import { useTheme } from '../../theme/themeStore.js';
import { useDocView } from './docView.js';
import { PlansSurface } from './PlansSurface.js';
import { AllocatedPaneFrame } from './shared/AllocatedPaneFrame.js';
import {
  dataIndexFromCursor,
  isCreateCursor,
  listRowCountWithCreate,
  onEnterCreateOrOpen,
} from './shared/createListRow.js';
import { usePaneUiClampedCursor } from './shared/useClampedCursor.js';

type PlansIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open' | 'spawnPlanner';

const CREATE_LABEL = '+ new plan';

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
  const rowCount = listRowCountWithCreate(view.rows.length);
  const { cursor, setCursor, moveDown, moveUp } = usePaneUiClampedCursor('plans', rowCount);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const rowIdAtCursor = useCallback((): string | null => {
    const dataIndex = dataIndexFromCursor(cursor);
    if (dataIndex === null) {
      return null;
    }
    return view.rows[dataIndex]?.id ?? null;
  }, [cursor, view.rows]);

  const onCreate = useCallback(() => {
    getPanelCreateActions().newPlan();
  }, []);

  const onRowClick = useCallback(
    (index: number) => {
      setCursor(index);
      onEnterCreateOrOpen(index, onCreate, (dataIndex) => {
        const id = view.rows[dataIndex]?.id;
        if (id !== undefined) {
          toggleDoc(id);
        }
      });
    },
    [onCreate, setCursor, toggleDoc, view.rows],
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
        { chord: { key: { return: true } }, intent: 'open', description: 'view doc / create' },
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
            if (isCreateCursor(cursor)) {
              return;
            }
            const id = rowIdAtCursor();
            if (id !== null) {
              void toggleFavorite(id);
            }
            return;
          }
          case 'open': {
            onEnterCreateOrOpen(cursor, onCreate, (dataIndex) => {
              const id = view.rows[dataIndex]?.id;
              if (id !== undefined) {
                toggleDoc(id);
              }
            });
            return;
          }
          case 'spawnPlanner': {
            if (isCreateCursor(cursor)) {
              return;
            }
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
    [
      bindings,
      cursor,
      moveDown,
      moveUp,
      onCreate,
      refresh,
      rowIdAtCursor,
      spawnPlanner,
      toggleDoc,
      toggleFavorite,
      view.rows,
    ],
  );
  usePanelKeymap('plans', keymap);

  return (
    <AllocatedPaneFrame id="plans" presentation={presentation}>
      <PlansSurface
        width={presentation.width}
        height={presentation.height}
        focused={presentation.focused}
        theme={theme}
        rows={view.rows}
        cursor={cursor}
        createLabel={CREATE_LABEL}
        status={view.status}
        error={view.error}
        onRowClick={onRowClick}
      />
    </AllocatedPaneFrame>
  );
});
