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
 *    - `open` is fired by `enter` — toggles the doc-view Stage pane ({@link ./DocPane.js}).
 *  - Row key: `name` (notes are keyed by filename, not an agent id).
 *
 * ## Phase 3: Pane + Ledger conversion
 * Converted to the layout primitives following {@link ./PlansPanel.tsx} (the Phase 2 reference). The
 * hand-rolled `<Box borderStyle>` + title `<Text>` chrome is now a {@link ./Pane.tsx Pane}
 * (inline-title border, focus color, the forwarded measure `ref`), and the hand-rolled
 * `NoteEntry`/`NotesList` map is now a {@link ./Ledger.tsx Ledger} (two-line single-column entries,
 * full-width highlight, alternating background, overflow windowing). What stayed EXACTLY the same:
 * the local `cursor` `useState`, the j/k/r/star/open keymap, the selector usage (`useNotesView`),
 * and the focus wiring (`useFocusRef`/`useEffectiveFocus`/`useMeasureFocus`).
 *
 * Two rendering rules the Pane + Ledger split imposes (copied from PlansPanel):
 *  - The Ledger owns the selection highlight (a full-width background on the cursor row), so
 *    `renderEntry` must NOT re-apply `inverse`. It uses `ctx.selected` only for the `▌` marker + the
 *    line-2 dim.
 *  - The Ledger renders nothing for an empty list, so the empty/loading/error chrome stays in the
 *    PANEL (as the Pane's children), branching to the Ledger only when there are rows.
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
import { useDocView } from './DocPane.js';
import { Ledger, type LedgerEntryContext } from './Ledger.js';
import { Pane } from './Pane.js';

const PANEL_ID: PanelId = 'notes';
const PANEL_TITLE = 'Notes';

/**
 * Fixed Ledger budget until the Pane measures and passes down its inner content size.
 *
 * TODO(Phase 3/4 — Pane-measures-inner-size handoff, see {@link ./Ledger.tsx}'s "Sizing" note and
 * {@link ./Pane.tsx}'s handoff): the Pane should measure its own inner rect and pass
 * `availableHeight`/`availableWidth` down so the Ledger's overflow window tracks the live panel
 * size. Until then this is a reasonable static budget — the Ledger clips via its window and the
 * Pane's `overflow="hidden"` is the hard safety clip regardless.
 */
const LEDGER_HEIGHT = 40;
const LEDGER_WIDTH = 40;

type NotesIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

/**
 * Render one note row as a two-line Ledger entry. Line 1: cursor marker + star + name. Line 2: char
 * count · updated time. The Ledger paints the full-width selection background and the alternating
 * shade, so this only uses `ctx.selected` for the `▌` marker + line-2 dim — it does NOT set
 * `inverse`. Single column (`maxColumns=1`), so `ctx.columns` is unused.
 */
function renderNoteEntry(row: NoteRowView, ctx: LedgerEntryContext): React.ReactNode {
  const marker = ctx.selected ? '▌' : ' ';
  const star = row.starred ? '★ ' : '';
  return (
    // The LedgerRow wraps this in a full-width `row` Box (with the highlight/alt-bg background); a
    // two-line entry composes its own `column` here. `flexGrow={1}` spans the background; `flexShrink={0}`
    // so Yoga doesn't drop a line.
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Text wrap="truncate">{`${marker} ${star}${row.name}`}</Text>
      <Text dimColor={!ctx.selected} wrap="truncate">
        {`  ${row.charCount} · ${row.updatedAt}`}
      </Text>
    </Box>
  );
}

/** The list body: empty/loading/error chrome (Ledger renders nothing for zero rows), else the
 * two-line entries via {@link Ledger} (in selector order, with the full-width selection highlight). */
function NotesList({
  view,
  cursor,
  focused,
}: {
  readonly view: NotesView;
  readonly cursor: number;
  readonly focused: boolean;
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
    <Ledger
      rows={view.rows}
      cursor={cursor}
      focused={focused}
      linesPerEntry={2}
      minColumns={1}
      maxColumns={1}
      availableHeight={LEDGER_HEIGHT}
      availableWidth={LEDGER_WIDTH}
      renderEntry={renderNoteEntry}
      rowKey={(row) => row.name}
    />
  );
}

/** The notes panel. Reads its slice, runs the selector, owns a local cursor, declares its keymap,
 * and paints a focus-highlighted Pane of two-line Ledger entries. `React.memo`'d (rule 1). */
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
        // alt+s stars the highlighted note (the dispatcher routes alt+s here when a panel — not
        // chat — is focused; the global layer never sees this panel's local cursor, rule 1).
        { chord: { input: 's', key: { meta: true } }, intent: 'star', description: 'star' },
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
    // The Pane owns the inline-title border + focus color + the forwarded measure `ref`. The list
    // body (Ledger, or the empty/loading/error chrome) is its children.
    <Pane ref={ref} title={PANEL_TITLE} focused={focused}>
      <NotesList
        view={view}
        cursor={Math.min(cursor, Math.max(rowCount - 1, 0))}
        focused={focused}
      />
    </Pane>
  );
});
