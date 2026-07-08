import { type JSX, memo, useCallback, useEffect, useMemo } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../../hooks/useAppStore.js';
import { useBindings, usePanelKeymap } from '../../hooks/useInputStores.js';
import type { PanelKeymap } from '../../input/keymap.js';
import type { PanePresentation } from '../../layout/paneLayoutTypes.js';
import { useNotesView } from '../../selectors/notesSelectors.js';
import { useTheme } from '../../theme/themeStore.js';
import { useDocView } from './docView.js';
import { NotesSurface } from './NotesSurface.js';
import { AllocatedPaneFrame } from './shared/AllocatedPaneFrame.js';
import { usePaneUiClampedCursor } from './shared/useClampedCursor.js';

type NotesIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

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
  const { cursor, moveDown, moveUp } = usePaneUiClampedCursor('notes', view.rows.length);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const rowNameAtCursor = useCallback(
    (): string | null => view.rows[cursor]?.name ?? null,
    [cursor, view.rows],
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
        status={view.status}
        error={view.error}
      />
    </AllocatedPaneFrame>
  );
});
