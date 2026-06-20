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
import { deriveAgentIdentity } from '../selectors/agentIdentity.js';
import { isChatPaneOpen } from '../selectors/conversationsSelectors.js';
import { HEALTH_EDGE_COLOR } from '../selectors/crowHealthSelectors.js';
import { type CrowRowView, type CrowsView, useCrowsView } from '../selectors/crowsSelectors.js';
import { murderConfirmStore, resetConfirmStore } from '../store/murder/murderConfirmStore.js';
import { toastStore } from '../store/toast/toastStore.js';
import { useTheme } from '../theme/themeStore.js';
import { Ledger, type LedgerEntryContext } from './Ledger.js';
import { Pane } from './Pane.js';

const PANEL_ID: PanelId = 'crows';
const PANEL_TITLE = 'Crows';

// The Ledger self-measures its own inner size now (see {@link ./Ledger.tsx}'s "Sizing" note), so no
// fixed budget is passed: its overflow window tracks the live panel size.

type CrowsIntent =
  | 'cursorDown'
  | 'cursorUp'
  | 'refresh'
  | 'toggleExpanded'
  | 'star'
  | 'openChat'
  | 'murder'
  | 'reset';

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
  // Indicator circle colour carries the health signal the old left-edge glyph used to: a red `○`
  // means "awaiting you with an open escalation", a green `●` means "healthily working", etc.
  const indicatorColor = HEALTH_EDGE_COLOR[row.health];
  return (
    <Box flexDirection="column" flexGrow={1} flexShrink={0}>
      <Text wrap="truncate">
        {/* FIXED-WIDTH leading gutter: a star (or space) cell, a space, then the readiness circle,
            a space, then the name — so names stay column-aligned whether or not the row is starred.
            `○` = ready for user input, `●` = working / not yet ready. */}
        {row.favorited ? '★' : ' '} <Text color={indicatorColor}>{row.working ? '●' : '○'}</Text>
        {` ${row.name}`}
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
 * The Ledger column-titles key for crows — a single dim legend explaining the readiness circle that
 * leads each crow line: `○` = ready for user input, `●` = working. The grouped section labels are
 * in-band rows; this top key explains what a crow LINE means. Rendered above the rows; it doesn't
 * participate in the flat cursor mapping (headers/keys are never selectable).
 */
function renderCrowsHeader(): React.ReactNode {
  return (
    <Box flexShrink={0}>
      <Text dimColor>{'  ○ ready  ● working'}</Text>
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
  onOverflow,
}: {
  readonly view: CrowsView;
  readonly expanded: boolean;
  readonly ledgerRows: readonly CrowLedgerRow[];
  readonly ledgerCursor: number;
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
      renderEntry={(ledgerRow, ctx) => renderCrowRow(ledgerRow, ctx, expanded)}
      header={renderCrowsHeader}
      rowKey={(ledgerRow) =>
        ledgerRow.kind === 'header' ? `h:${ledgerRow.group}` : `c:${ledgerRow.row.agentId}`
      }
      // The Ledger's rows are the FLAT array (interleaved section headers + crow rows), so the overflow
      // counts map against `ledgerRows.length`, NOT the crow-only count. KNOWN-HARMLESS over-count: hidden
      // section-header flat-rows are included, so `▴ N` / `▾ N` can over-count by the number of hidden
      // section headers. Documented-acceptable per the plan's uniformity invariant — do NOT special-case.
      onWindow={(win) => onOverflow({ above: win.start, below: ledgerRows.length - win.end })}
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
  const favorites = useAppStore((s) => s.favorites, shallow);
  const conversations = useAppStore((s) => s.conversations, shallow);
  // Favorites feed the selector so favorited crows sort to the top of their group + show `★ ` (9d).
  const view = useCrowsView(roster, favorites);
  // Rule 3: bus reached only through the dispatched actions.
  const refresh = useAppStore((s) => s.actions.roster.refresh);
  const resetCrow = useAppStore((s) => s.actions.roster.resetCrow);
  const toggleFavorite = useAppStore((s) => s.actions.favorites.toggle);
  const setActivePane = useAppStore((s) => s.actions.conversations.setActivePaneAgentId);
  const toggleChatPane = useAppStore((s) => s.actions.conversations.toggleChatPane);

  // Local UI state: cursor position (CROW rows only) + minimized/maximized toggle (rule 1).
  const [cursor, setCursor] = useState(0);
  const [expanded, setExpanded] = useState(false);
  // Scroll-overflow counts fed up from the Ledger's window (via the list's onOverflow) into the Pane
  // border's ▴/▾ indicators. Mapped against the FLAT row count in the list. Reset to {0,0} when there
  // are no crow rows (the Ledger doesn't render, so onWindow never fires to clear a stale count) — see
  // the rowCount===0 guard at the Pane below.
  const [overflow, setOverflow] = useState<{ above: number; below: number }>({
    above: 0,
    below: 0,
  });

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

  // Toggle the highlighted crow's chat pane on/off (item 9c). Resolves the cursor agent's identity
  // from the roster, computes its CURRENT open state (favorites default + override) so the toggle
  // flips it, and — when OPENING — pins it as the active pane. Reads the live slices imperatively at
  // call time so no stale closure on `conversations`/`favorites`.
  const openChatAtCursor = useCallback(() => {
    const agentId = agentIdAtCursor();
    if (agentId === null) return;
    const rosterRow = roster.rows.find((r) => r.agentId === agentId);
    const identity = rosterRow !== undefined ? deriveAgentIdentity(rosterRow) : null;
    if (identity === null) return;
    const currentlyOpen = isChatPaneOpen(identity, favorites, conversations.paneOverrides);
    toggleChatPane(agentId, currentlyOpen);
    if (!currentlyOpen) {
      setActivePane(agentId); // opening → make this the active chat pane (spec)
    }
  }, [agentIdAtCursor, roster, favorites, conversations, toggleChatPane, setActivePane]);

  // The favorite/star chord comes from the central registry (`panel.star`); `bindings` is a stable
  // identity that changes only on a settings change, so it is a safe keymap dep (no churn).
  const bindings = useBindings();

  // Rule 5: keymap as data + exhaustive intent handler; PanelKeymap in useMemo.
  const keymap: PanelKeymap<CrowsIntent> = useMemo(
    () => ({
      keymap: [
        // hjkl + arrows (item 5: every list cursor also accepts the arrow keys).
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
        // ctrl+m ARMS the murder confirm for the highlighted crow. The dispatcher's global layer
        // deliberately declines `global.murder` while this panel is focused so the chord lands here
        // and targets the LOCAL cursor row (rule 1). Declared BEFORE the plain-Enter `openChat`
        // entry: chord flags are subset-matched, so ctrl+return satisfies `{ return: true }` too —
        // first-match order is what keeps ctrl+m off the pane toggle (a plain Enter carries no ctrl
        // and can never match this entry, so the ordering costs Enter nothing). The confirm press
        // (`m`/ctrl+m while armed) is claimed by the dispatcher's pending check BEFORE this keymap,
        // so the plain-`m` min/max toggle below never fires during the confirm window.
        { chord: bindings.chordsFor('global.murder'), intent: 'murder', description: 'murder' },
        // Enter toggles the highlighted crow's chat pane on/off (item 9c).
        { chord: { key: { return: true } }, intent: 'openChat', description: 'toggle chat pane' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: { input: 'm' }, intent: 'toggleExpanded', description: 'toggle maximized' },
        // The command-modified chord (alt+f by default) stars the highlighted crow (dispatcher routes
        // it here when a panel is focused) AND keeps that crow's chat pane active (spec: "favorite
        // while chatting a crow stars it and keeps that chat pane active").
        { chord: bindings.chordsFor('panel.star'), intent: 'star', description: 'favorite' },
        // Plain `x` — two-press crow reset (kill the crow, ticket → ready). Both presses land HERE
        // (the chord only fires while this panel is focused), so the confirm needs no dispatcher
        // pending-check: the handler re-derives the cursor row and confirms only when it still
        // matches the armed target; otherwise the press re-arms for the new row. Registry-sourced
        // (`panel.resetCrow`) so the help overlay lists it.
        {
          chord: bindings.chordsFor('panel.resetCrow'),
          intent: 'reset',
          description: 'reset crow',
        },
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
          case 'openChat':
            openChatAtCursor();
            return;
          case 'star': {
            const agentId = agentIdAtCursor();
            if (agentId !== null) {
              void toggleFavorite(agentId);
              setActivePane(agentId); // keep this crow's chat pane active (spec)
            }
            return;
          }
          case 'murder': {
            const agentId = agentIdAtCursor();
            if (agentId === null) {
              return;
            }
            // Arm only — the kill itself is the shell's confirm handler (App.tsx), which submits
            // `agent.stop`; this panel never touches the bus (rule 3). The row's display name rides
            // along for the "press m again to murder <name>" toast.
            const name =
              view.sections.flatMap((s) => s.rows).find((r) => r.agentId === agentId)?.name ??
              agentId;
            murderConfirmStore.getState().arm({ agentId, name });
            return;
          }
          case 'reset': {
            const agentId = agentIdAtCursor();
            if (agentId === null) {
              return;
            }
            // Reset is ticket-scoped: only crow rows (which carry a ticket) are resettable. The
            // ticket id comes from the roster slice row, not the view row (rule 2 — the view stays
            // grouping-only).
            const ticketId = roster.rows.find((r) => r.agentId === agentId)?.ticketId ?? null;
            if (ticketId === null) {
              toastStore.getState().push('no ticket to reset for this row', { ttlMs: 4000 });
              return;
            }
            const pending = resetConfirmStore.getState().pending;
            if (pending !== null && pending.ticketId === ticketId) {
              // Second press on the same row within the TTL → confirm. Submit `crow.reset` via the
              // roster action (rule 3) and surface the outcome as a toast; the row/ticket updates
              // arrive via entity snapshots.
              resetConfirmStore.getState().clear();
              void resetCrow(ticketId)
                .then(() => {
                  toastStore.getState().push(`reset ${pending.name} → ready`, { ttlMs: 6000 });
                })
                .catch((error: unknown) => {
                  const message = error instanceof Error ? error.message : String(error);
                  toastStore.getState().push(message, { severity: 'error', ttlMs: 12000 });
                });
              return;
            }
            const name =
              view.sections.flatMap((s) => s.rows).find((r) => r.agentId === agentId)?.name ??
              agentId;
            resetConfirmStore.getState().arm({ ticketId, name });
            return;
          }
          default:
            return intent satisfies never;
        }
      },
    }),
    [
      moveCursor,
      refresh,
      resetCrow,
      toggleFavorite,
      setActivePane,
      agentIdAtCursor,
      openChatAtCursor,
      bindings,
      view,
      roster,
    ],
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
      overflowAbove={rowCount === 0 ? 0 : overflow.above}
      overflowBelow={rowCount === 0 ? 0 : overflow.below}
    >
      <CrowsList
        view={view}
        expanded={expanded}
        ledgerRows={ledgerRows}
        ledgerCursor={ledgerCursor}
        focused={focused}
        onOverflow={setOverflow}
      />
    </Pane>
  );
});
