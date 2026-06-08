/**
 * CrowsPanel — the crows-by-type right panel (panel 0, C9).
 *
 * Copied from {@link RosterPanel.tsx} per the C5 copy recipe. Changes vs. RosterPanel:
 *  - Slice: `useAppStore((s) => s.roster, shallow)` (same slice — crows come from roster).
 *  - Selector: `useCrowsView` from `crowsSelectors.ts` (groups by collaborator → planners →
 *    rogue → ticket; rule 2: ALL grouping/ordering is in the selector, not here).
 *  - Layout: sections with a header label, then two-line entries per row.
 *  - minimized / maximized: a `useState` boolean toggled by `'m'` key. Minimized = one line
 *    per crow (name + status only). Maximized = two lines (name+status, harness · model).
 *    The selector produces both shapes per row; the component picks based on `expanded`.
 *  - `PANEL_ID`: `'crows'` (unchanged — already in PanelId; no panels.ts edit needed).
 *
 * Rule 2 proof: the component never reads `row.role` or `row.ticketId` — those are the
 * selector's grouping inputs. The component receives `CrowsView.sections` and paints them.
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
  type CrowRowView,
  type CrowSection,
  type CrowsView,
  useCrowsView,
} from '../selectors/crowsSelectors.js';

const PANEL_ID: PanelId = 'crows';
const PANEL_TITLE = 'Crows';

type CrowsIntent = 'cursorDown' | 'cursorUp' | 'refresh' | 'toggleExpanded' | 'star';

/** Flatten the grouped sections into one cursor-ordered list of agent ids — the order the cursor
 * walks (sections in order, rows within each). Pure; lets `ctrl+s` resolve the highlighted crow. */
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
 * One crow row in minimized mode: one line (name + status).
 * Memoised on row + cursor flag so only changed-selected rows repaint.
 */
const CrowEntryMin = memo(function CrowEntryMin({
  row,
  selected,
}: {
  readonly row: CrowRowView;
  readonly selected: boolean;
}): React.JSX.Element {
  const marker = selected ? '▌' : ' ';
  return (
    <Text inverse={selected} wrap="truncate">
      {`${marker} ${row.name}  `}
      <Text color="cyan">{row.status}</Text>
    </Text>
  );
});

/**
 * One crow row in maximized mode: two lines (name+status, then harness · model).
 * Memoised on row + cursor flag.
 */
const CrowEntryMax = memo(function CrowEntryMax({
  row,
  selected,
}: {
  readonly row: CrowRowView;
  readonly selected: boolean;
}): React.JSX.Element {
  const marker = selected ? '▌' : ' ';
  return (
    <Box flexDirection="column">
      <Text inverse={selected} wrap="truncate">
        {`${marker} ${row.name}  `}
        <Text color="cyan">{row.status}</Text>
      </Text>
      <Text dimColor={!selected} inverse={selected} wrap="truncate">
        {`  ${row.harness} · ${row.model}`}
      </Text>
    </Box>
  );
});

/** One section: a dimmed header label followed by its crow entries. */
function CrowSectionView({
  section,
  expanded,
  cursorStart,
  cursor,
}: {
  readonly section: CrowSection;
  readonly expanded: boolean;
  /** The global cursor index of the first row in this section. */
  readonly cursorStart: number;
  readonly cursor: number;
}): React.JSX.Element {
  return (
    <Box flexDirection="column">
      <Text dimColor bold>
        {section.label}
      </Text>
      {section.rows.map((row, i) => {
        const selected = cursorStart + i === cursor;
        return expanded ? (
          <CrowEntryMax key={row.agentId} row={row} selected={selected} />
        ) : (
          <CrowEntryMin key={row.agentId} row={row} selected={selected} />
        );
      })}
    </Box>
  );
}

/**
 * The list body: loading/error/empty chrome, else the grouped sections.
 * Split out so keymap + focus wiring in `CrowsPanel` stays readable.
 */
function CrowsList({
  view,
  expanded,
  cursor,
}: {
  readonly view: CrowsView;
  readonly expanded: boolean;
  readonly cursor: number;
}): React.JSX.Element {
  if (view.status === 'error') {
    return <Text color="red">{`error: ${view.error ?? 'unknown'}`}</Text>;
  }
  if (view.status === 'loading' && view.isEmpty) {
    return <Text dimColor>loading…</Text>;
  }
  if (view.isEmpty) {
    return <Text dimColor>no crows</Text>;
  }

  // Build cursor offsets: each section's first row has a global cursor index.
  const sectionOffsets: number[] = [];
  let offset = 0;
  for (const section of view.sections) {
    sectionOffsets.push(offset);
    offset += section.rows.length;
  }

  return (
    <Box flexDirection="column">
      {view.sections.map((section, si) => (
        <CrowSectionView
          key={section.group}
          section={section}
          expanded={expanded}
          cursorStart={sectionOffsets[si] ?? 0}
          cursor={cursor}
        />
      ))}
    </Box>
  );
}

/**
 * Count the total rows across all sections (for cursor clamping).
 * Pure utility used only by CrowsPanel to avoid ad-hoc reduce in the render.
 */
function totalRows(view: CrowsView): number {
  let n = 0;
  for (const s of view.sections) n += s.rows.length;
  return n;
}

/**
 * The crows panel. Reads the roster slice, runs `useCrowsView` for type-grouped sections,
 * owns a local cursor + expanded flag, declares its keymap, and paints a focus-highlighted
 * bordered box. `React.memo`'d (rule 1) so it re-renders only when its own state changes.
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

  // Local UI state: cursor position + minimized/maximized toggle (rule 1).
  const [cursor, setCursor] = useState(0);
  const [expanded, setExpanded] = useState(false);
  const rowCount = totalRows(view);

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

  // Rule 5: keymap as data + exhaustive intent handler; PanelKeymap in useMemo.
  const keymap: PanelKeymap<CrowsIntent> = useMemo(
    () => ({
      keymap: [
        { chord: { input: 'j' }, intent: 'cursorDown', description: 'next crow' },
        { chord: { input: 'k' }, intent: 'cursorUp', description: 'prev crow' },
        { chord: { input: 'r' }, intent: 'refresh', description: 'refresh' },
        { chord: { input: 'm' }, intent: 'toggleExpanded', description: 'toggle maximized' },
        // ctrl+s stars the highlighted crow (dispatcher routes ctrl+s here when a panel is focused)
        // AND keeps that crow's chat pane active (spec: "ctrl+s while chatting a crow stars it and
        // keeps that chat pane active").
        { chord: { input: 's', key: { ctrl: true } }, intent: 'star', description: 'star' },
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
    [moveCursor, refresh, toggleFavorite, setActivePane, agentIdAtCursor],
  );
  usePanelKeymap(PANEL_ID, keymap);

  // Focus highlight + rect registration — identical across every panel (rule 5).
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === PANEL_ID;
  useMeasureFocus(PANEL_ID, ref);

  const clampedCursor = Math.min(cursor, Math.max(rowCount - 1, 0));
  const modeLabel = expanded ? '[max]' : '[min]';

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
        {`${PANEL_TITLE} `}
        <Text dimColor>{modeLabel}</Text>
      </Text>
      <CrowsList view={view} expanded={expanded} cursor={clampedCursor} />
    </Box>
  );
});
