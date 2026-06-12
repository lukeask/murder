/**
 * RosterPanel — THE reference panel component. Every future left/right list panel is a copy of
 * this file with its slice, selector, panel id, and row layout swapped. It is the concrete answer
 * to "what does a panel look like?", so it is written to make the *correct* shape the easy one.
 *
 * The anatomy a copy keeps verbatim (this is the contract a panel implements):
 *  1. **Pure function of a slice + `React.memo`** (rule 1). The component reads exactly one slice
 *     via `useAppStore(selector, shallow)`, runs the C3 selector to a view-model, and paints it.
 *     `React.memo` + the narrow selector is the *standard* — a sibling slice ref-swapping never
 *     re-renders this panel. The component knows nothing about the bus (rule 3).
 *  2. **Presentation comes from the selector, never computed here** (rule 2). Sort order, the
 *     two-line column tuple, truncation, sentinels — all already done by {@link useRosterView}. The
 *     component's job is layout (where the cells go on screen), not formatting (what they say).
 *  3. **Local UI state is `useState`, not store state** (rule 1). The cursor/scroll/expanded a panel
 *     owns are view concerns, not domain state — they live here, never in the slice. (Roster's
 *     reference list keeps a cursor; richer panels add scroll/expanded the same way.)
 *  4. **Keyboard via keymap-as-data** (rule 5). The panel *declares* a {@link PanelKeymap} and hands
 *     it to {@link usePanelKeymap}; the root dispatcher routes keys to it only when it is focused. The
 *     panel never calls `useInput` and never sees a raw key.
 *  5. **Focus highlight + measured rect.** The border colour reads {@link useEffectiveFocus} (the
 *     re-home invariant, applied for free); the box registers its rect via {@link useMeasureFocus}
 *     so `ctrl+vim` directional nav can target it.
 *
 * To make panel X (e.g. notes) — the copy recipe, in full:
 *   - copy this file to `XPanel.tsx`;
 *   - swap the slice read `useAppStore((s) => s.roster, shallow)` → `s.x`, and the selector
 *     `useRosterView` → `useXView` (its view-model already carries X's two-line tuple — rule 2);
 *   - swap `PANEL_ID` to X's {@link PanelId} and the title;
 *   - swap the two `<Text>` lines in {@link RosterEntry} for X's row fields;
 *   - declare X's own keymap intents (the `RosterIntent` union and the `onIntent` switch);
 *   - dispatch X's action for any intent that hits the bus (`useAppStore((s) => s.actions.x.…)`).
 * No focus/measure/memo/test glue is re-derived — those calls are identical across panels.
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
import {
  type RosterRowView,
  type RosterView,
  useRosterView,
} from '../selectors/rosterSelectors.js';
import { useTheme } from '../theme/themeStore.js';

/** The panel id this component owns — its `ctrl+<n>` digit, focus identity, and region all derive
 * from this one constant (see `src/input/panels.ts`). A copy changes only this and the slice. */
const PANEL_ID: PanelId = 'crows';

/** Human title for the panel header — kept beside the panel id so a copy renames both together. */
const PANEL_TITLE = 'Crows';

/** The panel's declared intents — the closed action-name union this panel handles. The keymap and
 * the `onIntent` switch are both typed against it, so an un-handled intent is a compile error. A
 * copy renames these to its own actions (`'open' | 'star'`, …). */
type RosterIntent = 'cursorDown' | 'cursorUp' | 'refresh';

/**
 * One roster entry rendered as a **two-line** block (the plan's "two-lineheight entries"). Line one
 * is the primary identity (name + status); line two is the secondary metadata (harness · model).
 * Both lines come from the selector's view-model — this function only places them. The cursor row is
 * marked with a leading caret and inverse so the local cursor is visible without colour alone.
 *
 * Memoised on the row + cursor flag so re-rendering the list (e.g. the cursor moving) only repaints
 * the two entries whose selected-ness changed, not every row.
 */
const RosterEntry = memo(function RosterEntry({
  row,
  selected,
}: {
  readonly row: RosterRowView;
  readonly selected: boolean;
}): React.JSX.Element {
  const theme = useTheme();
  const marker = selected ? '▌' : ' ';
  return (
    <Box flexDirection="column">
      <Text inverse={selected} wrap="truncate">
        {`${marker} ${row.name}  `}
        <Text color={theme.heading}>{row.status}</Text>
      </Text>
      <Text dimColor={!selected} inverse={selected} wrap="truncate">
        {`  ${row.harness} · ${row.model}`}
      </Text>
    </Box>
  );
});

/** The list body: empty/loading/error chrome, else the two-line entries. Split out so the keymap +
 * focus wiring in {@link RosterPanel} stays readable and the pure render is independently testable. */
function RosterList({
  view,
  cursor,
}: {
  readonly view: RosterView;
  readonly cursor: number;
}): React.JSX.Element {
  const theme = useTheme();
  if (view.status === 'error') {
    return <Text color={theme.error}>{`error: ${view.error ?? 'unknown'} (r to retry)`}</Text>;
  }
  if (view.status === 'loading' && view.isEmpty) {
    return <Text dimColor>loading…</Text>;
  }
  if (view.isEmpty) {
    return <Text dimColor>no crows</Text>;
  }
  return (
    <Box flexDirection="column">
      {view.rows.map((row, index) => (
        <RosterEntry key={row.agentId} row={row} selected={index === cursor} />
      ))}
    </Box>
  );
}

/**
 * The reference panel. Reads its slice, runs the selector, owns a local cursor, declares its keymap,
 * and paints a focus-highlighted bordered box of two-line entries. `React.memo`'d (rule 1) so it
 * re-renders only when its own selected state changes.
 */
export const RosterPanel = memo(function RosterPanel(): React.JSX.Element {
  // Rule 1: read exactly this slice (shallow so an unrelated slice ref-swap doesn't re-render us),
  // then rule 2: the selector produces the render-ready, sorted, two-line view-model.
  const roster = useAppStore((s) => s.roster, shallow);
  const view = useRosterView(roster);
  // Rule 3: the bus is reached only through the dispatched action; the component never imports it.
  const refresh = useAppStore((s) => s.actions.roster.refresh);
  const theme = useTheme();

  // Rule 1: cursor is local UI state — a view concern, not domain state, so it lives here.
  const [cursor, setCursor] = useState(0);
  const rowCount = view.rows.length;

  // Clamp the cursor inside the current list. Wrapped in a callback so the keymap below is stable.
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

  // Rule 5: declare the keymap as data + an intent handler. Memoised so the registry effect in
  // `usePanelKeymap` doesn't churn; the handler closes over the live `moveCursor`/`refresh`.
  const keymap: PanelKeymap<RosterIntent> = useMemo(
    () => ({
      keymap: [
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'cursorDown',
          description: 'next crow',
        },
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'cursorUp',
          description: 'prev crow',
        },
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
            // Exhaustiveness: a new intent without a case is a compile error here.
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
      borderColor={focused ? theme.active : theme.inactive}
      paddingX={1}
      flexGrow={1}
    >
      <Text bold color={focused ? theme.focus : theme.text}>
        {PANEL_TITLE}
      </Text>
      <RosterList view={view} cursor={Math.min(cursor, Math.max(rowCount - 1, 0))} />
    </Box>
  );
});
