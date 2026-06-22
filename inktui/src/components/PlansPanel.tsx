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
 *
 * ## Phase 2: the END-TO-END Pane + Ledger reference
 * This is the FIRST panel converted to the new layout primitives, and the template Phase 3 copies for
 * the other five panels. The hand-rolled `<Box borderStyle>` + title `<Text>` chrome is now a
 * {@link ./Pane.tsx Pane} (inline-title border, focus color, the forwarded measure `ref`), and the
 * hand-rolled `PlanEntry`/`PlansList` map is now a {@link ./Ledger.tsx Ledger} (full-width highlight,
 * alternating background, overflow windowing) over the shared {@link ./ResourceRow.tsx} two-line row
 * (the doc-style entry plans/notes/reports all paint). What stayed EXACTLY the same: the local `cursor`
 * `useState`, the j/k/r/star/open keymap, the selector usage (`usePlansView`), and the focus wiring
 * (`useFocusRef`/`useEffectiveFocus`/`useMeasureFocus`). Only the rendering changed.
 *
 * Two rendering rules the Pane + Ledger split imposes (Phase 3 must keep these):
 *  - The Ledger owns the selection highlight (a full-width blue background on the cursor row), so
 *    `renderEntry` must NOT re-apply `inverse` — that would fight the Ledger's background. The shared
 *    {@link ./ResourceRow.tsx} entry uses `ctx.selected` only for the line-2 dim (and an optional
 *    cursor marker, disabled by default).
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
import { type PlansView, usePlansView } from '../selectors/plansSelectors.js';
import { useTheme } from '../theme/themeStore.js';
import { useDocView } from './DocPane.js';
import { Ledger } from './Ledger.js';
import { Pane } from './Pane.js';
import { renderResourceEntry, renderResourceHeader } from './ResourceRow.js';

const PANEL_ID: PanelId = 'plans';
const PANEL_TITLE = 'Plans';

// The Ledger self-measures its own inner size now (see {@link ./Ledger.tsx}'s "Sizing" note), so no
// fixed budget is passed: its overflow window tracks the live panel size, the cursor stays on screen.

type PlansIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open' | 'spawnPlanner';

// The two-line row + header come from the shared {@link ./ResourceRow.tsx} renderer (plans/notes/
// reports all paint the identical doc-style entry; per-panel variation — the tree indent, sort, star
// float — lives in {@link ../selectors/plansSelectors.js} and is baked into `row.name`/`row.starred`
// before the renderer sees it). `PlanRowView` is structurally a `ResourceRowFields`, so the slice
// rows pass straight through.

/** The list body: empty/loading/error chrome (Ledger renders nothing for zero rows), else the
 * two-line entries via {@link Ledger} (in selector order, with the full-width selection highlight). */
function PlansList({
  view,
  cursor,
  focused,
  onOverflow,
}: {
  readonly view: PlansView;
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
    return <Text dimColor>no plans</Text>;
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
      rowKey={(row) => row.id}
      onWindow={(win) => onOverflow({ above: win.start, below: view.rows.length - win.end })}
    />
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
  const spawnPlanner = useAppStore((s) => s.actions.plans.spawnPlanner);
  const toggleDoc = useDocView('plan');

  // Fetch on first open. The Rail only mounts a panel while visible, so this runs when the user opens
  // Plans (ctrl+4) — the lazy fetch that replaces the (removed) eager startup prime. It moves the
  // slice off `idle`, so the gated invalidation entry in store.ts keeps it live thereafter. The
  // selector renders the empty/loading state until rows arrive. `refresh` is a stable store action.
  useEffect(() => {
    void refresh();
  }, [refresh]);

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

  // The cursor row's stable favorite id (its un-indented filename) — local cursor (rule 1).
  const rowIdAtCursor = useCallback((): string | null => {
    const clamped = Math.min(cursor, Math.max(rowCount - 1, 0));
    return view.rows[clamped]?.id ?? null;
  }, [cursor, rowCount, view.rows]);

  // The favorite/star chord comes from the central registry (`panel.star`), so the modifier setting
  // and any rebind are honoured. `bindings` is a stable identity that changes only on a settings
  // change — safe as the keymap's sole input-related dep (no per-render re-registration).
  const bindings = useBindings();

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
        // `p` spawns a planning agent over the highlighted plan (the same intent the staged plan
        // doc binds — both route through `actions.plans.spawnPlanner`, one defaults home).
        { chord: { input: 'p' }, intent: 'spawnPlanner', description: 'spawn planner' },
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
    [moveCursor, refresh, toggleFavorite, toggleDoc, spawnPlanner, rowIdAtCursor, bindings],
  );
  usePanelKeymap(PANEL_ID, keymap);

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
      <PlansList
        view={view}
        cursor={Math.min(cursor, Math.max(rowCount - 1, 0))}
        focused={focused}
        onOverflow={setOverflow}
      />
    </Pane>
  );
});
