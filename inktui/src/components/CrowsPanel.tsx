/**
 * CrowsPanel — the crows-by-type right panel (panel 0, C9).
 *
 * Copied from {@link RosterPanel.tsx} per the C5 copy recipe. Changes vs. RosterPanel:
 *  - Slice: `useAppStore((s) => s.roster, shallow)` (same slice — crows come from roster).
 *  - Selector: `useCrowsView` from `crowsSelectors.ts` (groups by collaborator → planners →
 *    rogue → ticket; rule 2: ALL grouping/ordering is in the selector, not here).
 *  - Layout: sections with a header label, then one/two-line entries per row.
 *  - minimized / maximized: a `useState` boolean toggled by `'m'` key. Minimized = one line
 *    per crow (name + status only). Maximized = two lines (name+status, harness · model).
 *  - `PANEL_ID`: `'crows'`.
 *
 * Rule 2 proof: the component never reads `row.role` or `row.ticketId` — those are the
 * selector's grouping inputs. The component receives `CrowsView.sections` and paints them.
 *
 * ## Phase 3: Pane + Ledger conversion — the trickiest panel (section headers + flat cursor)
 * Converted to the layout primitives. The bordered chrome is now a {@link ./Pane.tsx Pane} (with the
 * `[min]`/`[max]` mode label passed as a pre-styled `titleExtra`). The grouped sections are now a
 * single {@link ./Ledger.tsx Ledger} via **option (ii): flatten sections + headers into one row
 * list** where header rows are a row-kind `renderEntry` switches on. This keeps the cursor + the
 * Ledger windowing on a single flat array, which is cleaner than nesting a Ledger per section.
 *
 * The cursor-index trap and how it's handled:
 *  - The panel's `cursor` counts CROW rows only (unchanged — keymap, `moveCursor`, `agentIdAtCursor`,
 *    and starring all still index into `flatAgentIds`). The Ledger, however, highlights by its OWN
 *    flat-array index. So we derive a separate `ledgerCursor` = the flat-array index of the
 *    cursor-th crow row and pass THAT to the Ledger. Header rows therefore never equal `ledgerCursor`
 *    and are never highlighted — for free.
 *  - The flat `{kind:'header'|'crow'}` list is built in the component via `useMemo` over
 *    `view.sections` (this is LAYOUT, not formatting — the selector stays untouched). `rowKey` is
 *    unique across kinds (`h:${group}` / `c:${agentId}`) so React keys don't collide.
 *  - `linesPerEntry` is driven by `expanded` (1 minimized / 2 maximized). The health left-edge glyph
 *    color + section labels are preserved; `renderEntry` sets NO `inverse` (Ledger owns the
 *    full-width highlight); the glyph uses `ctx.selected` (`▌`) vs `▎`.
 *
 * ## Two documented compromises of folding headers into a uniform-height Ledger:
 *  1. Header rows are 1 line, but the Ledger reserves `linesPerEntry` lines per row uniformly. In
 *     `[max]` mode (linesPerEntry=2) a header occupies a 2-line slot, so the overflow window's height
 *     math slightly over-counts header rows. Harmless under the fixed 40-line budget (the Pane's
 *     `overflow="hidden"` is the hard clip); revisit if/when the Pane passes a measured height.
 *  2. The Ledger's alternating-background parity counts by absolute row index INCLUDING headers, so a
 *     header row may occasionally pick up the subtle shade. Acceptable — the shade is intentionally
 *     subtle and headers are dim-bold regardless.
 */

import { Box, Text } from 'ink';
import { memo, useCallback, useMemo, useState } from 'react';
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
import { HEALTH_EDGE_COLOR } from '../selectors/crowHealthSelectors.js';
import { type CrowRowView, type CrowsView, useCrowsView } from '../selectors/crowsSelectors.js';
import type { Theme } from '../theme/buildTheme.js';
import { useTheme } from '../theme/themeStore.js';
import { Ledger, type LedgerEntryContext } from './Ledger.js';
import { Pane } from './Pane.js';

const PANEL_ID: PanelId = 'crows';
const PANEL_TITLE = 'Crows';

// The Ledger self-measures its own inner size now (see {@link ./Ledger.tsx}'s "Sizing" note), so no
// fixed budget is passed: its overflow window tracks the live panel size.

type CrowsIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'toggleExpanded' | 'star';

/**
 * A flattened Ledger row: either a section header (a label) or a crow row. Headers are interleaved
 * in cursor-walk order (sections in order, rows within each). This is layout shaping in the
 * component — the selector still owns the grouping/ordering (rule 2); we only flatten its sections.
 */
type CrowLedgerRow =
  | { readonly kind: 'header'; readonly group: string; readonly label: string }
  | { readonly kind: 'crow'; readonly row: CrowRowView };

/** Flatten the grouped sections into one cursor-ordered list of agent ids — the order the cursor
 * walks (sections in order, rows within each). Pure; lets `alt+s` resolve the highlighted crow. */
function flatAgentIds(view: CrowsView): readonly string[] {
  const ids: string[] = [];
  for (const section of view.sections) {
    for (const row of section.rows) {
      ids.push(row.agentId);
    }
  }
  return ids;
}

/**
 * Build the flat Ledger row list (headers interleaved with crow rows) AND a parallel map from the
 * crow-only cursor index to its flat-array index. The Ledger highlights by flat index, but the
 * panel's cursor counts crow rows only — so `crowToFlat[cursor]` is the `ledgerCursor` to pass down.
 */
function buildFlatRows(view: CrowsView): {
  readonly rows: readonly CrowLedgerRow[];
  readonly crowToFlat: readonly number[];
} {
  const rows: CrowLedgerRow[] = [];
  const crowToFlat: number[] = [];
  for (const section of view.sections) {
    rows.push({ kind: 'header', group: section.group, label: section.label });
    for (const row of section.rows) {
      crowToFlat.push(rows.length);
      rows.push({ kind: 'crow', row });
    }
  }
  return { rows, crowToFlat };
}

/**
 * Render one flat row. Headers are a dim-bold label line; crow rows show the health left-edge glyph
 * + name + status (and, in maximized mode, a second harness · model line). The Ledger owns the
 * full-width highlight + alt-bg, so this sets NO `inverse` — only `ctx.selected` flips the glyph to
 * `▌`. `expanded` drives the second line (it matches the Ledger's `linesPerEntry`).
 */
function renderCrowRow(
  ledgerRow: CrowLedgerRow,
  ctx: LedgerEntryContext,
  expanded: boolean,
  theme: Theme,
): React.ReactNode {
  if (ledgerRow.kind === 'header') {
    return (
      <Box flexDirection="column" flexGrow={1} flexShrink={0}>
        <Text dimColor bold wrap="truncate">
          {ledgerRow.label}
        </Text>
      </Box>
    );
  }
  const { row } = ledgerRow;
  const edgeColor = HEALTH_EDGE_COLOR[row.health];
  return (
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Text wrap="truncate">
        <Text color={edgeColor}>{ctx.selected ? '▌' : '▎'}</Text>
        {` ${row.name}  `}
        <Text color={theme.heading}>{row.status}</Text>
      </Text>
      {expanded ? (
        <Text dimColor={!ctx.selected} wrap="truncate">
          {`  ${row.harness} · ${row.model}`}
        </Text>
      ) : null}
    </Box>
  );
}

/**
 * The Ledger column-titles key for crows — a single dim line labeling the crow row layout: a 2-col
 * leading gutter (matching the glyph + space) then `crow · status` (bug 1). The grouped section labels
 * are in-band rows; this top key explains what a crow LINE means. Rendered above the rows; it doesn't
 * participate in the flat cursor mapping (headers/keys are never selectable).
 */
function renderCrowsHeader(): React.ReactNode {
  return (
    <Box flexShrink={0}>
      <Text dimColor>{'  crow · status'}</Text>
    </Box>
  );
}

/** The list body: loading/error/empty chrome (Ledger renders nothing for zero rows), else the
 * flattened sections + crow rows via {@link Ledger}. */
function CrowsList({
  view,
  expanded,
  ledgerRows,
  ledgerCursor,
  focused,
}: {
  readonly view: CrowsView;
  readonly expanded: boolean;
  readonly ledgerRows: readonly CrowLedgerRow[];
  readonly ledgerCursor: number;
  readonly focused: boolean;
}): React.JSX.Element {
  const theme = useTheme();
  if (view.status === 'error') {
    return <Text color={theme.error}>{`error: ${view.error ?? 'unknown'}`}</Text>;
  }
  if (view.status === 'loading' && view.isEmpty) {
    return <Text dimColor>loading…</Text>;
  }
  if (view.isEmpty) {
    return <Text dimColor>no crows</Text>;
  }
  return (
    <Ledger
      rows={ledgerRows}
      cursor={ledgerCursor}
      focused={focused}
      linesPerEntry={expanded ? 2 : 1}
      minColumns={1}
      maxColumns={1}
      renderEntry={(ledgerRow, ctx) => renderCrowRow(ledgerRow, ctx, expanded, theme)}
      header={renderCrowsHeader}
      rowKey={(ledgerRow) =>
        ledgerRow.kind === 'header' ? `h:${ledgerRow.group}` : `c:${ledgerRow.row.agentId}`
      }
    />
  );
}

/**
 * The crows panel. Reads the roster slice, runs `useCrowsView` for type-grouped sections,
 * owns a local cursor + expanded flag, declares its keymap, and paints a focus-highlighted
 * Pane. `React.memo`'d (rule 1) so it re-renders only when its own state changes.
 */
export const CrowsPanel = memo(function CrowsPanel(): React.JSX.Element {
  // Rule 1: narrow selector (shallow) — only re-renders when the roster slice ref-changes.
  // Rule 2: view comes pre-grouped from the selector; no role/ticketId logic here.
  const roster = useAppStore((s) => s.roster, shallow);
  const view = useCrowsView(roster);
  // Rule 3: bus reached only through the dispatched actions.
  const refresh = useAppStore((s) => s.actions.roster.refresh);
  const toggleFavorite = useAppStore((s) => s.actions.favorites.toggle);
  const setActivePane = useAppStore((s) => s.actions.conversations.setActivePaneAgentId);

  // Local UI state: cursor position (CROW rows only) + minimized/maximized toggle (rule 1).
  const [cursor, setCursor] = useState(0);
  const [expanded, setExpanded] = useState(false);

  // Flatten sections + headers into one Ledger list (layout, not formatting). `crowToFlat` maps the
  // crow-only cursor to its flat-array index so the Ledger highlights the right row (headers excluded).
  const { rows: ledgerRows, crowToFlat } = useMemo(() => buildFlatRows(view), [view]);
  const rowCount = crowToFlat.length;

  const moveCursor = useCallback(
    (delta: number) => {
      setCursor((current) => {
        if (rowCount === 0) return 0;
        const next = current + delta;
        return Math.min(Math.max(next, 0), rowCount - 1);
      });
    },
    [rowCount],
  );

  // The highlighted crow's agentId at call time — local cursor (rule 1), flattened section order.
  const agentIdAtCursor = useCallback((): string | null => {
    const ids = flatAgentIds(view);
    const clamped = Math.min(cursor, Math.max(ids.length - 1, 0));
    return ids[clamped] ?? null;
  }, [cursor, view]);

  // The favorite/star chord comes from the central registry (`panel.star`); `bindings` is a stable
  // identity that changes only on a settings change, so it is a safe keymap dep (no churn).
  const bindings = useBindings();

  // Rule 5: keymap as data + exhaustive intent handler; PanelKeymap in useMemo.
  const keymap: PanelKeymap<CrowsIntent> = useMemo(
    () => ({
      keymap: [
        { chord: { input: 'j' }, intent: 'cursorDown', description: 'next crow' },
        { chord: { input: 'k' }, intent: 'cursorUp', description: 'prev crow' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: { input: 'm' }, intent: 'toggleExpanded', description: 'toggle maximized' },
        // The command-modified chord (alt+f by default) stars the highlighted crow (dispatcher routes
        // it here when a panel is focused) AND keeps that crow's chat pane active (spec: "favorite
        // while chatting a crow stars it and keeps that chat pane active").
        { chord: bindings.chordsFor('panel.star'), intent: 'star', description: 'favorite' },
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
          case 'toggleExpanded':
            setExpanded((e) => !e);
            return;
          case 'star': {
            const agentId = agentIdAtCursor();
            if (agentId !== null) {
              void toggleFavorite(agentId);
              setActivePane(agentId); // keep this crow's chat pane active (spec)
            }
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [moveCursor, refresh, toggleFavorite, setActivePane, agentIdAtCursor, bindings],
  );
  usePanelKeymap(PANEL_ID, keymap);

  // Focus highlight + rect registration — identical across every panel (rule 5).
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === PANEL_ID;
  useMeasureFocus(PANEL_ID, ref);

  const clampedCursor = Math.min(cursor, Math.max(rowCount - 1, 0));
  // Map the crow-only cursor to the Ledger's flat-array index (headers excluded). When there are no
  // crow rows the empty chrome renders instead, so 0 is a safe default.
  const ledgerCursor = crowToFlat[clampedCursor] ?? 0;
  const modeLabel = expanded ? '[max]' : '[min]';

  return (
    // `titleExtra` is the `[min]`/`[max]` mode label, pre-styled per Pane's contract (the caller owns
    // its color — Pane renders it outside the green/white title segment).
    <Pane
      ref={ref}
      title={PANEL_TITLE}
      focused={focused}
      titleExtra={<Text dimColor>{` ${modeLabel}`}</Text>}
    >
      <CrowsList
        view={view}
        expanded={expanded}
        ledgerRows={ledgerRows}
        ledgerCursor={ledgerCursor}
        focused={focused}
      />
    </Pane>
  );
});
