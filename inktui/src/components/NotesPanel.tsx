/**
 * NotesPanel — the notes list, panel 2 (ctrl+2).
 *
 * Copied from {@link ./RosterPanel.tsx} per the C5 copy recipe; extended by C11 with the two
 * generalized doc-panel interactions — **star** (`ctrl+s`) and **open** (`enter`):
 *  - Slice: `s.notes` (via `useNotesView`), plus `s.favorites` so starred sort to the top (rule 2 —
 *    the sort lives in the selector via {@link ../selectors/favoritesSelectors.js stableSortStarredFirst}).
 *  - `PANEL_ID`: `'notes'`.
 *  - Row layout: line 1 = star marker + name; line 2 = char count · updated time.
 *  - Intents: `'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open'`.
 *    - `star` is fired by `ctrl+s` — the dispatcher routes `ctrl+s` to the focused panel's keymap
 *      (it is global-for-chat-only; see dispatcher.ts). The panel stars its OWN cursor row (rule 1 —
 *      the cursor stays local; the global layer never sees it). This is THE generalized starring
 *      pattern plans/reports copy.
 *    - `open` is fired by `enter` — toggles the in-layout doc view ({@link ./DocViewMode.js}).
 *  - Row key: `name` (notes are keyed by filename, not an agent id).
 *
 * Everything else is verbatim glue from the roster panel.
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
import { type NoteRowView, type NotesView, useNotesView } from '../selectors/notesSelectors.js';
import { useDocView } from './DocViewMode.js';

const PANEL_ID: PanelId = 'notes';
const PANEL_TITLE = 'Notes';

type NotesIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

/**
 * One note entry rendered as a two-line block.
 * Line 1: star marker + name (the document filename / title).
 * Line 2: char count · updated time (formatted by the selector — rule 2).
 *
 * Memoised on row + cursor + starred so only changed entries repaint.
 */
const NoteEntry = memo(function NoteEntry({
  row,
  selected,
  starred,
}: {
  readonly row: NoteRowView;
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
function NotesList({
  view,
  cursor,
}: {
  readonly view: NotesView;
  readonly cursor: number;
}): React.JSX.Element {
  if (view.status === 'error') {
    return <Text color="red">{`error: ${view.error ?? 'unknown'}`}</Text>;
  }
  if (view.status === 'loading' && view.isEmpty) {
    return <Text dimColor>loading…</Text>;
  }
  if (view.isEmpty) {
    return <Text dimColor>no notes</Text>;
  }
  return (
    <Box flexDirection="column">
      {view.rows.map((row, index) => (
        <NoteEntry key={row.name} row={row} selected={index === cursor} starred={row.starred} />
      ))}
    </Box>
  );
}

/** The notes panel. Reads its slice, runs the selector, owns a local cursor, declares its keymap,
 * and paints a focus-highlighted bordered box of two-line entries. `React.memo`'d (rule 1). */
export const NotesPanel = memo(function NotesPanel(): React.JSX.Element {
  // Rule 1: read exactly these slices (shallow), rule 2: selector produces the view-model.
  const notes = useAppStore((s) => s.notes, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const view = useNotesView(notes, favorites);
  // Rule 3: bus reached only through the dispatched actions.
  const refresh = useAppStore((s) => s.actions.notes.refresh);
  const toggleFavorite = useAppStore((s) => s.actions.favorites.toggle);
  // enter on a note toggles the in-layout read-only doc view (rule 3: open/close via docView action).
  const toggleDoc = useDocView('note');

  // Rule 1: cursor is local UI state.
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

  // Resolve the cursor row's favorite id (its filename) at call time — the cursor is local (rule 1),
  // so the panel itself is the only place that knows which row `ctrl+s`/`enter` acts on.
  const rowNameAtCursor = useCallback((): string | null => {
    const clamped = Math.min(cursor, Math.max(rowCount - 1, 0));
    return view.rows[clamped]?.name ?? null;
  }, [cursor, rowCount, view.rows]);

  // Rule 5: keymap as data, wrapped in useMemo so the registry effect doesn't churn.
  const keymap: PanelKeymap<NotesIntent> = useMemo(
    () => ({
      keymap: [
        { chord: { input: 'j' }, intent: 'cursorDown', description: 'next note' },
        { chord: { input: 'k' }, intent: 'cursorUp', description: 'prev note' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        // ctrl+s stars the highlighted note (the dispatcher routes ctrl+s here when a panel — not
        // chat — is focused; the global layer never sees this panel's local cursor, rule 1).
        { chord: { input: 's', key: { ctrl: true } }, intent: 'star', description: 'star' },
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

  // Focus highlight + rect registration — identical across every panel (rule 5).
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
      <NotesList view={view} cursor={Math.min(cursor, Math.max(rowCount - 1, 0))} />
    </Box>
  );
});
