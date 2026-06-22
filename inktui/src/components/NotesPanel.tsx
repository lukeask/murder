/**
 * NotesPanel — the notes list, panel 2 (ctrl+2).
 *
 * Copied from {@link ./RosterPanel.tsx} per the C5 copy recipe; extended by C11 with the two
 * generalized doc-panel interactions — **star** (`ctrl+s`) and **open** (`enter`):
 *  - Slice: `s.notes` (via `useNotesView`), plus `s.favorites` so starred sort to the top (rule 2 —
 *    the sort lives in the selector via {@link ../selectors/favoritesSelectors.js stableSortStarredFirst}).
 *  - `PANEL_ID`: `'notes'`.
 *  - Row layout: the shared {@link ./ResourceRow.tsx} two-line entry — line 1 = optional star + name,
 *    line 2 = char count · updated time.
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
 * full-width highlight, alternating background, overflow windowing) over the shared
 * {@link ./ResourceRow.tsx} two-line row (the doc-style entry plans/notes/reports all paint).
 * What stayed EXACTLY the same:
 * the local `cursor` `useState`, the j/k/r/star/open keymap, the selector usage (`useNotesView`),
 * and the focus wiring (`useFocusRef`/`useEffectiveFocus`/`useMeasureFocus`).
 *
 * Two rendering rules the Pane + Ledger split imposes (copied from PlansPanel):
 *  - The Ledger owns the selection highlight (a full-width background on the cursor row), so
 *    `renderEntry` must NOT re-apply `inverse`. The shared {@link ./ResourceRow.tsx} entry uses
 *    `ctx.selected` only for the line-2 dim (and an optional cursor marker, disabled by default).
 *  - The Ledger renders nothing for an empty list, so the empty/loading/error chrome stays in the
 *    PANEL (as the Pane's children), branching to the Ledger only when there are rows.
 */

import { Text } from 'ink';
import { memo, useCallback, useEffect, useMemo, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../hooks/useAppStore.js';
import {
  useBindings,
  useEffectiveFocus,
  useFocusRef,
  useMeasureFocus,
  usePanelKeymap,
} from '../hooks/useInputStores.js';
import type { PanelKeymap } from '../input/keymap.js';
import type { PanelId } from '../input/panels.js';
import { type NotesView, useNotesView } from '../selectors/notesSelectors.js';
import { useTheme } from '../theme/themeStore.js';
import { useDocView } from './DocPane.js';
import { Ledger } from './Ledger.js';
import { Pane } from './Pane.js';
import { renderResourceEntry, renderResourceHeader } from './ResourceRow.js';

const PANEL_ID: PanelId = 'notes';
const PANEL_TITLE = 'Notes';

// The Ledger self-measures its own inner size now (see {@link ./Ledger.tsx}'s "Sizing" note), so no
// fixed budget is passed: its overflow window tracks the live panel size, the cursor stays on screen.

type NotesIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

// The two-line row + header come from the shared {@link ./ResourceRow.tsx} renderer (plans/notes/
// reports paint the identical doc-style entry — flush-left, star shown only when starred, no forced
// cursor glyph). Notes' star-float sort lives in {@link ../selectors/notesSelectors.js} and is baked
// into `row.starred` before the renderer sees it. `NoteRowView` is structurally a `ResourceRowFields`.

/** The list body: empty/loading/error chrome (Ledger renders nothing for zero rows), else the
 * two-line entries via {@link Ledger} (in selector order, with the full-width selection highlight). */
function NotesList({
  view,
  cursor,
  focused,
  onOverflow,
}: {
  readonly view: NotesView;
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
      renderEntry={renderResourceEntry}
      header={renderResourceHeader}
      rowKey={(row) => row.name}
      onWindow={(win) => onOverflow({ above: win.start, below: view.rows.length - win.end })}
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

  // Fetch on first open. The Rail only mounts a panel while visible, so this runs when the user opens
  // Notes (ctrl+5) — the lazy fetch that replaces the (removed) eager startup prime. It moves the
  // slice off `idle`, so the gated invalidation entry in store.ts keeps it live thereafter. The
  // selector renders the empty/loading state until rows arrive. `refresh` is a stable store action.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Rule 1: cursor is local UI state.
  const [cursor, setCursor] = useState(0);
  // Scroll-overflow counts fed up from the Ledger's window (via the list's onOverflow) into the Pane
  // border's ▴/▾ indicators. Reset to {0,0} when there are no rows (the Ledger doesn't render, so
  // onWindow never fires to clear a stale count) — see the rowCount===0 guard at the Pane below.
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

  // Resolve the cursor row's favorite id (its filename) at call time — the cursor is local (rule 1),
  // so the panel itself is the only place that knows which row `ctrl+s`/`enter` acts on.
  const rowNameAtCursor = useCallback((): string | null => {
    const clamped = Math.min(cursor, Math.max(rowCount - 1, 0));
    return view.rows[clamped]?.name ?? null;
  }, [cursor, rowCount, view.rows]);

  // The favorite/star chord comes from the central registry (`panel.star`); `bindings` is a stable
  // identity that changes only on a settings change, so it is a safe keymap dep (no churn).
  const bindings = useBindings();

  // Rule 5: keymap as data, wrapped in useMemo so the registry effect doesn't churn.
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
        // The command-modified chord (alt+f by default) stars the highlighted note. The dispatcher
        // routes it here when a panel — not chat — is focused; the global layer never sees this
        // panel's local cursor, rule 1.
        { chord: bindings.chordsFor('panel.star'), intent: 'star', description: 'favorite' },
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
    [moveCursor, refresh, toggleFavorite, toggleDoc, rowNameAtCursor, bindings],
  );
  usePanelKeymap(PANEL_ID, keymap);

  // Focus highlight + rect registration — identical across every panel (rule 5).
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === PANEL_ID;
  useMeasureFocus(PANEL_ID, ref);

  return (
    // The Pane owns the inline-title border + focus color + the forwarded measure `ref`. The list
    // body (Ledger, or the empty/loading/error chrome) is its children.
    <Pane
      ref={ref}
      title={PANEL_TITLE}
      focused={focused}
      overflowAbove={rowCount === 0 ? 0 : overflow.above}
      overflowBelow={rowCount === 0 ? 0 : overflow.below}
    >
      <NotesList
        view={view}
        cursor={Math.min(cursor, Math.max(rowCount - 1, 0))}
        focused={focused}
        onOverflow={setOverflow}
      />
    </Pane>
  );
});
