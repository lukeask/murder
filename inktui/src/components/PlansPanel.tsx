/**
 * PlansPanel — the plans list, panel 1 (ctrl+1). Replaces the C5/C6 placeholder.
 *
 * Copied from {@link ./NotesPanel.tsx} (the doc-panel pattern: two-line entries, local cursor,
 * star + open-doc keymap). Differs from notes in TWO ways, both confined to the selector (rule 2):
 *  - **Parent-plan indentation:** child plans are listed under their parent, name indented 4 spaces;
 *    a child's recency bubbles the parent's ordering position. The slice is flat (a `parent` field);
 *    {@link ../selectors/plansSelectors.js usePlansView} builds the tree + indent + ordering.
 *  - **Star reconciliation:** starred plans float to the top *as groups* (a starred parent floats
 *    its whole subtree). Also in the selector. The component just paints `PlanRowView`s in order.
 *
 * The component is otherwise identical doc-panel glue: `ctrl+s` stars the highlighted plan, `enter`
 * toggles the in-layout doc view. `name` in the view-model is already indented for children, so the
 * row's stable `id` (its un-indented filename) is what the star/open actions act on.
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
import { type PlanRowView, type PlansView, usePlansView } from '../selectors/plansSelectors.js';
import { useDocView } from './DocViewMode.js';

const PANEL_ID: PanelId = 'plans';
const PANEL_TITLE = 'Plans';

type PlansIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

/**
 * One plan entry rendered as a two-line block. Line 1: star marker + (already-indented) name.
 * Line 2: char count · updated time. Memoised on row + cursor + starred.
 */
const PlanEntry = memo(function PlanEntry({
  row,
  selected,
}: {
  readonly row: PlanRowView;
  readonly selected: boolean;
}): React.JSX.Element {
  const marker = selected ? '▌' : ' ';
  const star = row.starred ? '★ ' : '';
  return (
    // `flexShrink={0}`: when the list overflows the panel's clamped height, Yoga must NOT shrink the
    // entries to fit (that drops/samples rows — "every ~3rd line shown"). With shrink off, each entry
    // keeps height 2 and the overflow is clipped to a contiguous top slice. (Real windowing later.)
    <Box flexDirection="column" flexShrink={0}>
      <Text inverse={selected} wrap="truncate">
        {`${marker} ${star}${row.name}`}
      </Text>
      <Text dimColor={!selected} inverse={selected} wrap="truncate">
        {`  ${row.charCount} · ${row.updatedAt}`}
      </Text>
    </Box>
  );
});

/** The list body: empty/loading/error chrome, else the two-line entries (in selector order). */
function PlansList({
  view,
  cursor,
}: {
  readonly view: PlansView;
  readonly cursor: number;
}): React.JSX.Element {
  if (view.status === 'error') {
    return <Text color="red">{`error: ${view.error ?? 'unknown'}`}</Text>;
  }
  if (view.status === 'loading' && view.isEmpty) {
    return <Text dimColor>loading…</Text>;
  }
  if (view.isEmpty) {
    return <Text dimColor>no plans</Text>;
  }
  return (
    <Box flexDirection="column">
      {view.rows.map((row, index) => (
        <PlanEntry key={row.id} row={row} selected={index === cursor} />
      ))}
    </Box>
  );
}

/** The plans panel. Reads the plans + favorites slices, runs `usePlansView` (tree + indent + star
 * order), owns a local cursor, declares its keymap, paints a focus-highlighted box. `React.memo`'d. */
export const PlansPanel = memo(function PlansPanel(): React.JSX.Element {
  const plans = useAppStore((s) => s.plans, shallow);
  const favorites = useAppStore((s) => s.favorites, shallow);
  const view = usePlansView(plans, favorites);
  const refresh = useAppStore((s) => s.actions.plans.refresh);
  const toggleFavorite = useAppStore((s) => s.actions.favorites.toggle);
  const toggleDoc = useDocView('plan');

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

  // The cursor row's stable favorite id (its un-indented filename) — local cursor (rule 1).
  const rowIdAtCursor = useCallback((): string | null => {
    const clamped = Math.min(cursor, Math.max(rowCount - 1, 0));
    return view.rows[clamped]?.id ?? null;
  }, [cursor, rowCount, view.rows]);

  const keymap: PanelKeymap<PlansIntent> = useMemo(
    () => ({
      keymap: [
        { chord: { input: 'j' }, intent: 'cursorDown', description: 'next plan' },
        { chord: { input: 'k' }, intent: 'cursorUp', description: 'prev plan' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
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
          default:
            return intent satisfies never;
        }
      },
    }),
    [moveCursor, refresh, toggleFavorite, toggleDoc, rowIdAtCursor],
  );
  usePanelKeymap(PANEL_ID, keymap);

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
      <PlansList view={view} cursor={Math.min(cursor, Math.max(rowCount - 1, 0))} />
    </Box>
  );
});
