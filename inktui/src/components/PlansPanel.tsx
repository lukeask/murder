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
 * alternating background, overflow windowing). What stayed EXACTLY the same: the local `cursor`
 * `useState`, the j/k/r/star/open keymap, the selector usage (`usePlansView`), and the focus wiring
 * (`useFocusRef`/`useEffectiveFocus`/`useMeasureFocus`). Only the rendering changed.
 *
 * Two rendering rules the Pane + Ledger split imposes (Phase 3 must keep these):
 *  - The Ledger owns the selection highlight (a full-width blue background on the cursor row), so
 *    `renderEntry` must NOT re-apply `inverse` — that would fight the Ledger's background. The entry
 *    uses `ctx.selected` only for the `▌` marker and the line-2 dim.
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
import { type PlanRowView, type PlansView, usePlansView } from '../selectors/plansSelectors.js';
import { useDocView } from './DocPane.js';
import { Ledger, type LedgerEntryContext } from './Ledger.js';
import { Pane } from './Pane.js';

const PANEL_ID: PanelId = 'plans';
const PANEL_TITLE = 'Plans';

// The Ledger self-measures its own inner size now (see {@link ./Ledger.tsx}'s "Sizing" note), so no
// fixed budget is passed: its overflow window tracks the live panel size, the cursor stays on screen.

type PlansIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'star' | 'open';

/**
 * Render one plan row as a two-line Ledger entry. Line 1: cursor marker + star + (already-indented)
 * name. Line 2: char count · updated time. The Ledger paints the full-width selection background and
 * the alternating-row shade, so this only uses `ctx.selected` for the `▌` marker + line-2 dim — it
 * does NOT set `inverse` (that would fight the Ledger's background). Single column (`maxColumns=1`),
 * so `ctx.columns` is unused. Memo-free: it's a plain render callback the Ledger drives per visible row.
 */
function renderPlanEntry(row: PlanRowView, ctx: LedgerEntryContext): React.ReactNode {
  const marker = ctx.selected ? '▌' : ' ';
  // FIXED-WIDTH star gutter (bug 2): `★ ` when starred, two spaces otherwise, so the name column
  // starts at the same x on every row regardless of star state. Both occupy 2 columns.
  const star = row.starred ? '★ ' : '  ';
  return (
    // The LedgerRow wraps this in a `row` Box (with the full-width highlight/alt-bg background), so a
    // two-line entry must compose its own `column` here. `flexGrow={1}` lets the background span the
    // full row width behind both lines; `flexShrink={0}` so Yoga doesn't sample/drop a line. The
    // line-2 indent is `marker(1)+space(1)+star(2)` = 4 cols so `charCount` sits under `name`.
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Text wrap="truncate">{`${marker} ${star}${row.name}`}</Text>
      <Text dimColor={!ctx.selected} wrap="truncate">
        {`    ${row.charCount} · ${row.updatedAt}`}
      </Text>
    </Box>
  );
}

/**
 * The Ledger column-titles key — a dim two-line block (matching `linesPerEntry=2`) labeling what the
 * entry lines mean: `name` over `size · updated`. Aligned to the entry layout — the 4-space leading
 * indent matches {@link renderPlanEntry}'s leading gutters (marker(1) + space(1) + star(2)) on line 1
 * and its identical line-2 indent — so the labels sit directly above the data columns. THE reference
 * header shape Phase 3 panels copy: a `header={renderPlansHeader}` prop that returns this two-line
 * key. (`columns` is unused here — plans is single-column; a multi-column panel keys each field per
 * `columns`.)
 */
function renderPlansHeader(): React.ReactNode {
  return (
    <Box flexDirection="column" flexShrink={0}>
      <Text dimColor>{'    name'}</Text>
      <Text dimColor>{'    size · updated'}</Text>
    </Box>
  );
}

/** The list body: empty/loading/error chrome (Ledger renders nothing for zero rows), else the
 * two-line entries via {@link Ledger} (in selector order, with the full-width selection highlight). */
function PlansList({
  view,
  cursor,
  focused,
}: {
  readonly view: PlansView;
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
      renderEntry={renderPlanEntry}
      header={renderPlansHeader}
      overflowIndent={4}
      rowKey={(row) => row.id}
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
    // The Pane owns the inline-title border + focus color + the forwarded measure `ref`. The list
    // body (Ledger, or the empty/loading/error chrome) is its children.
    <Pane ref={ref} title={PANEL_TITLE} focused={focused}>
      <PlansList
        view={view}
        cursor={Math.min(cursor, Math.max(rowCount - 1, 0))}
        focused={focused}
      />
    </Pane>
  );
});
