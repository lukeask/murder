import { type JSX, memo, useCallback, useEffect, useMemo } from 'react';
import { shallow } from 'zustand/shallow';
import { getPanelCreateActions } from '../../create/panelCreateActions.js';
import { useAppStore } from '../../hooks/useAppStore.js';
import { useBindings, usePanelKeymap } from '../../hooks/useInputStores.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import { useNotesView } from '../../selectors/notesSelectors.js';
import { useTheme } from '../../theme/themeStore.js';
import { useDocView } from './docView.js';
import { NotesSurface } from './NotesSurface.js';
import { AllocatedPaneFrame } from './shared/AllocatedPaneFrame.js';
import {
  dataIndexFromCursor,
  isCreateCursor,
  listRowCountWithCreate,
  onEnterCreateOrOpen,
} from './shared/createListRow.js';
import { usePaneUiClampedCursor } from './shared/useClampedCursor.js';

type NotesIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

const CREATE_LABEL = '+ new note';

export interface NotesControllerProps {
  readonly presentation: PanePresentation;
}

export const NotesController = memo(function NotesController({
  presentation,
}: NotesControllerProps): JSX.Element {
  const notes = useAppStore((state) => state.notes, shallow);
  const favorites = useAppStore((state) => state.favorites, shallow);
  const refresh = useAppStore((state) => state.actions.notes.refresh);
  const toggleFavorite = useAppStore((state) => state.actions.favorites.toggle);
  const bindings = useBindings();
  const toggleDoc = useDocView('note');
  const view = useNotesView(notes, favorites);
  const theme = useTheme();
  const rowCount = listRowCountWithCreate(view.rows.length);
  const { cursor, setCursor, moveDown, moveUp } = usePaneUiClampedCursor('notes', rowCount);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const rowNameAtCursor = useCallback((): string | null => {
    const dataIndex = dataIndexFromCursor(cursor);
    if (dataIndex === null) {
      return null;
    }
    return view.rows[dataIndex]?.name ?? null;
  }, [cursor, view.rows]);

  const onCreate = useCallback(() => {
    getPanelCreateActions().quickNote();
  }, []);

  const onRowClick = useCallback(
    (index: number) => {
      setCursor(index);
      onEnterCreateOrOpen(index, onCreate, (dataIndex) => {
        const name = view.rows[dataIndex]?.name;
        if (name !== undefined) {
          toggleDoc(name);
        }
      });
    },
    [onCreate, setCursor, toggleDoc, view.rows],
  );

  const keymap: PanelKeymap<NotesIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next note',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev note',
        },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: bindings.chordsFor('panel.star'), intent: 'star', description: 'favorite' },
        { chord: { key: { return: true } }, intent: 'open', description: 'view doc / create' },
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
            const name = rowNameAtCursor();
            if (name !== null) {
              void toggleFavorite(name);
            }
            return;
          }
          case 'open': {
            onEnterCreateOrOpen(cursor, onCreate, (dataIndex) => {
              const name = view.rows[dataIndex]?.name;
              if (name !== undefined) {
                toggleDoc(name);
              }
            });
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
      rowNameAtCursor,
      toggleDoc,
      toggleFavorite,
      view.rows,
    ],
  );
  usePanelKeymap('notes', keymap);

  return (
    <AllocatedPaneFrame id="notes" presentation={presentation}>
      <NotesSurface
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
