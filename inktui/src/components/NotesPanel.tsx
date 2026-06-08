/**
 * NotesPanel — the notes list, panel 2 (ctrl+2).
 *
 * Copied from {@link ./RosterPanel.tsx} per the C5 copy recipe. Changes vs. the roster:
 *  - Slice: `s.notes` (via `useNotesView`).
 *  - `PANEL_ID`: `'notes'`.
 *  - Row layout: line 1 = name; line 2 = char count · updated time (from {@link NoteRowView}).
 *  - Empty chrome: `'no notes'`.
 *  - Intents: `'cursorDown' | 'cursorUp' | 'refresh'` (same set as the roster — reused because
 *    the note panel's basic navigation is identical; the keymap descriptions differ).
 *  - Row key: `name` (notes are keyed by filename, not an agent id).
 *
 * Everything else — `React.memo`, `useAppStore` narrow selector, `usePanelKeymap`, `useMemo`
 * keymap, `useFocusRef`, `useEffectiveFocus`, `useMeasureFocus` — is verbatim from the roster
 * panel. These are the framework glue that every panel gets for free by copying.
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

const PANEL_ID: PanelId = 'notes';
const PANEL_TITLE = 'Notes';

type NotesIntent = 'cursorDown' | 'cursorUp' | 'refresh';

/**
 * One note entry rendered as a two-line block.
 * Line 1: name (the document filename / title).
 * Line 2: char count · updated time (formatted by the selector — rule 2).
 *
 * Memoised on row + cursor flag so only the two entries whose selected-ness changes repaint.
 */
const NoteEntry = memo(function NoteEntry({
  row,
  selected,
}: {
  readonly row: NoteRowView;
  readonly selected: boolean;
}): React.JSX.Element {
  const marker = selected ? '▌' : ' ';
  return (
    <Box flexDirection="column">
      <Text inverse={selected} wrap="truncate">
        {`${marker} ${row.name}`}
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
        <NoteEntry key={row.name} row={row} selected={index === cursor} />
      ))}
    </Box>
  );
}

/** The notes panel. Reads its slice, runs the selector, owns a local cursor, declares its keymap,
 * and paints a focus-highlighted bordered box of two-line entries. `React.memo`'d (rule 1). */
export const NotesPanel = memo(function NotesPanel(): React.JSX.Element {
  // Rule 1: read exactly this slice (shallow), rule 2: selector produces the view-model.
  const notes = useAppStore((s) => s.notes, shallow);
  const view = useNotesView(notes);
  // Rule 3: bus reached only through the dispatched action.
  const refresh = useAppStore((s) => s.actions.notes.refresh);

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

  // Rule 5: keymap as data, wrapped in useMemo so the registry effect doesn't churn.
  const keymap: PanelKeymap<NotesIntent> = useMemo(
    () => ({
      keymap: [
        { chord: { input: 'j' }, intent: 'cursorDown', description: 'next note' },
        { chord: { input: 'k' }, intent: 'cursorUp', description: 'prev note' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
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
          default:
            return intent satisfies never;
        }
      },
    }),
    [moveCursor, refresh],
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
