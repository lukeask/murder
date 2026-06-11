/**
 * Stage — the borderless CENTER region (the new subsystem; spec › "`Stage` — the center region").
 *
 * The Stage tiles **chat-history panes** beside (Phase 4b) document panes. It fills whatever the two
 * Rails leave (full width when both rails are off) — `flexGrow={1}` inside the Shell's Body row.
 *
 * ## What Phase 4a builds: chat-history panes
 * One {@link Pane} per favorited crow, MOVED here out of the old {@link ./CrowChatPanel.js} (which
 * stacked under {@link ./CrowsPanel.js} in the right Rail). Each pane shows that crow's turn history
 * (selector `useConversationTurns`) and is a focusable **Stage pane** so `alt+h/j/k/l` can reach it:
 *  - focus id = `stage:chat:<agentId>` (a {@link StagePaneId}). NOT a {@link PanelId} — chat panes are
 *    not toggleable; they appear/disappear as crows are favorited. They are reached ONLY via hjkl
 *    directional nav, never a `alt+<digit>` toggle (the dispatcher's digit path is untouched).
 *  - it registers its measured rect via {@link useMeasureFocus} (widened to {@link FocusId}) so the
 *    geometry kernel has its position; on unmount the rect is dropped (`unmeasure`) and focus re-homes
 *    to chat — the re-home invariant, applied to Stage panes exactly as to panels.
 *  - border/title color flips green when the pane holds the effective focus
 *    ({@link useEffectiveFocus} === its id), gray/white otherwise — the same scheme as every panel.
 *
 * ### History scroll (landed)
 * A focused chat pane declares a tiny keymap (`j`/`k` = scroll its turn window) via the keymap
 * registry, which Phase 4a widened to be keyed by {@link FocusId}. So `j`/`k` move the visible
 * window within the focused pane (panel-local concern, rule 1 — the window offset is `useState`),
 * while `alt+j`/`alt+k` move focus pane-to-pane (the global directional layer). Same split as the
 * panels' cursor-vs-nav. The pane registers its keymap only while it is the focused pane (so the
 * registry holds at most one chat-pane keymap and a blurred pane's `j` doesn't fight the focused
 * one); the registry key is the pane's own `stage:chat:<agentId>` so the dispatcher routes to it.
 *
 * ## Rules
 *  - Rule 1: each pane is `React.memo`; window offset is local `useState`; no bus knowledge.
 *  - Rule 2: which panes + turn formatting come from `conversationsSelectors` — none here.
 *  - Rule 3: send stays the conversations action (unchanged; not wired here — chat input owns send).
 *  - Rule 5: NO `useInput`. The pane declares a keymap to the registry; the one root dispatcher routes.
 *
 * ## Phase 4b: the document pane (landed)
 * When a document is open (`docView.open`), it renders as a {@link StageDocPane} ({@link Pane}) to the
 * RIGHT of the chat panes when the terminal is wide (landscape), stacking BELOW them when narrow
 * (portrait) — the same orientation reflow the Rails use, so the doc reads as a tall column beside the
 * chat in landscape and a wide strip under it in portrait. The doc pane slots into the SAME
 * {@link StagePaneId} scheme: focus id `stage:doc:<name>`, the same `useFocusRef` +
 * `useMeasureFocus(id, ref)` wiring, the same `useEffectiveFocus() === id` highlight (all inside
 * {@link ./DocPane.js}). No focus-model change was needed — `StagePaneId` + `mountedStagePanesOf`
 * already cover it; the Stage only had to read the `docView.open` slice key (so opening a doc
 * re-renders it) and add the pane. The doc pane is keyed by the open doc's NAME so opening a different
 * doc remounts it (resetting its scroll offset + re-registering its keymap under the new focus id).
 *
 * The doc-view used to be an in-layout *mode* painting into the Overlay; it was retired in favour of a
 * Stage pane — see {@link ./DocPane.js}'s header for why (a doc is a focusable thing on the Stage you
 * can nav away from, not a focus-takeover modal).
 */

import { Box, type DOMElement, measureElement, Text } from 'ink';
import { type JSX, memo, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { shallow } from 'zustand/shallow';
import { useAppStore } from '../hooks/useAppStore.js';
import {
  useEffectiveFocus,
  useFocusRef,
  useMeasureFocus,
  usePanelKeymap,
} from '../hooks/useInputStores.js';
import { useOrientation } from '../hooks/useOrientation.js';
import type { FocusId, StagePaneId } from '../input/focusStore.js';
import type { PanelKeymap } from '../input/keymap.js';
import type { AgentIdentity } from '../selectors/agentIdentity.js';
import {
  type ChatTurn,
  useConversationTurns,
  useOpenChatPanes,
} from '../selectors/conversationsSelectors.js';
import type { ConversationsState } from '../store/conversations/conversationsSlice.js';
import type { OpenDoc } from '../store/docView/docViewSlice.js';
import type { FavoritesState } from '../store/favorites/favoritesSlice.js';
import type { RosterState } from '../store/roster/rosterSlice.js';
import { useTheme } from '../theme/themeStore.js';
import { computeScrollThumb, Scrollbar, StageDocPane } from './DocPane.js';
import { Pane } from './Pane.js';

/** The Stage focus id for a crow's chat pane. The single place the `stage:chat:` scheme is minted, so
 * the id format (and the Phase 4b `stage:doc:` sibling) stays consistent. */
function chatPaneFocusId(agentId: string): StagePaneId {
  return `stage:chat:${agentId}`;
}

/** Human label for the crow's kind, shown dim in the title's `titleExtra` slot. */
function kindLabel(kind: AgentIdentity['kind']): string {
  switch (kind) {
    case 'collaborator':
      return 'collab';
    case 'planner':
      return 'planner';
    case 'rogue':
      return 'rogue';
    default:
      return 'ticket';
  }
}

/** How many turns scroll past per `j`/`k` (the window step). */
const SCROLL_STEP = 1;
/** Fallback turn-window size before the fill box has been measured (first paint or sizeless test
 * render). Once {@link measureElement} reports a real height that value drives the window. */
const FALLBACK_HEIGHT = 20;

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/** Format one turn for display: `›`/`·` on the first line, two-space indent on continuations. */
export function formatTurnText(turn: ChatTurn): string {
  const marker = turn.speaker === 'user' ? '›' : '·';
  return turn.text
    .split('\n')
    .map((line, i) => (i === 0 ? `${marker} ${line}` : `  ${line}`))
    .join('\n');
}

/** One chat turn block. Speaker determines the color. Renders the whole turn as a single Ink
 * `<Text>` (like {@link ./TextInput.js MultiLineText} in the new-plan form — commit 892d6ae) so long
 * lines soft-wrap to the pane width instead of truncating with an ellipsis. */
const TurnLine = memo(function TurnLine({ turn }: { readonly turn: ChatTurn }): JSX.Element {
  const theme = useTheme();
  const color =
    turn.speaker === 'user'
      ? theme.success
      : turn.speaker === 'assistant'
        ? theme.text
        : turn.speaker === 'tool'
          ? theme.warning
          : turn.speaker === 'plan'
            ? theme.heading
            : turn.speaker === 'notice'
              ? theme.error
              : theme.muted;
  return (
    <Box flexShrink={0}>
      <Text color={color}>{formatTurnText(turn)}</Text>
    </Box>
  );
});

/**
 * One crow's chat-history Pane — a focusable Stage pane. Owns its scroll window (`useState`, rule 1),
 * declares `j`/`k` to the keymap registry ONLY while focused (so exactly one chat pane's history
 * scroll is live, and a blurred pane never claims `j`), and flips the Pane's focus color when it holds
 * the effective focus. The Pane's outer box carries the focus ref so `useMeasureFocus` registers the
 * whole bordered region's rect for directional nav (matching the panel recipe in {@link ./Pane.tsx}).
 */
const ChatPane = memo(function ChatPane({
  identity,
  conversations,
}: {
  readonly identity: AgentIdentity;
  readonly conversations: ConversationsState;
}): JSX.Element {
  const focusId: FocusId = chatPaneFocusId(identity.agentId);
  const turns = useConversationTurns(identity.agentId, conversations);

  // Focus highlight + rect registration — the same recipe as every panel (rule 5), but with the
  // Stage-pane focus id. useMeasureFocus drops the rect on unmount → focus re-homes to chat.
  const ref = useFocusRef();
  const focused = useEffectiveFocus() === focusId;
  useMeasureFocus(focusId, ref);

  // Local scroll offset (rule 1): how many turns are hidden ABOVE the window's top. 0 = pinned to the
  // newest turns (the bottom). Clamped to the available scroll range when rendering so a shrinking
  // transcript can't strand the window past its end.
  const [scrollUp, setScrollUp] = useState(0);

  // Measured window height — the Ledger fill-box pattern (mirrors StageDocPane). The fill box below
  // is flexGrow so measureElement reports the room we HAVE, not the rows we drew. Fallback covers
  // first paint and sizeless test renders.
  const boxRef = useRef<DOMElement | null>(null);
  const [measuredHeight, setMeasuredHeight] = useState(0);
  useLayoutEffect(() => {
    if (boxRef.current === null) return;
    const { height } = measureElement(boxRef.current);
    if (height !== measuredHeight) setMeasuredHeight(height);
  });
  const effectiveHeight = measuredHeight > 0 ? measuredHeight : FALLBACK_HEIGHT;

  const maxScrollUp = Math.max(turns.length - effectiveHeight, 0);
  const clampedScroll = Math.min(scrollUp, maxScrollUp);

  // History-scroll keymap (rule 5: declared, not handled). `j`/`k` move the window; `alt+j`/`alt+k`
  // are the global directional-nav layer (pane-to-pane), so they never reach here. Registered only
  // while focused — the registry then holds at most one chat-pane keymap. Memoised on the scroll
  // bounds so the handler closes over a fresh `maxScrollUp` without re-registering every render.
  const keymap: PanelKeymap<ScrollIntent> = useMemo(
    () => ({
      keymap: [
        { chord: [{ input: 'k' }, { key: { upArrow: true } }], intent: 'scrollUp', description: 'older' },
        { chord: [{ input: 'j' }, { key: { downArrow: true } }], intent: 'scrollDown', description: 'newer' },
      ],
      onIntent(intent) {
        if (intent === 'scrollUp') {
          setScrollUp((s) => Math.min(s + SCROLL_STEP, maxScrollUp));
        } else {
          setScrollUp((s) => Math.max(s - SCROLL_STEP, 0));
        }
      },
    }),
    [maxScrollUp],
  );
  // Register only while focused so a blurred pane doesn't own `j`/`k` (no-op keymap otherwise). An
  // empty keymap when blurred means the registry entry exists but matches nothing — the dispatcher
  // only consults the FOCUSED id's entry anyway, so this is belt-and-suspenders for clarity.
  usePanelKeymap(focusId, focused ? keymap : EMPTY_KEYMAP);

  // The visible window: the effectiveHeight newest turns shifted up by the (clamped) scroll offset.
  // Slice arithmetic keeps the most recent turns by default (scroll 0 → the tail).
  const end = turns.length - clampedScroll;
  const start = Math.max(end - effectiveHeight, 0);
  const visibleTurns = turns.slice(start, end);
  const thumb = computeScrollThumb(turns.length, start, effectiveHeight);

  return (
    <Pane
      ref={ref}
      title={identity.label}
      focused={focused}
      titleExtra={<Text dimColor>{`[${kindLabel(identity.kind)}]`}</Text>}
      paddingRight={0}
    >
      {/* Fill box: sizes to the Pane's inner content area (flexGrow + overflow hidden), so
          measureElement reports the room we HAVE. Text column grows; scrollbar column is fixed. */}
      <Box ref={boxRef} flexDirection="row" flexGrow={1} minHeight={0} overflow="hidden">
        <Box flexDirection="column" flexGrow={1} minHeight={0} overflow="hidden">
          {visibleTurns.length === 0 ? (
            <Text dimColor>no history</Text>
          ) : (
            visibleTurns.map((turn, i) => <TurnLine key={turn.blockId ?? `${start + i}`} turn={turn} />)
          )}
        </Box>
        <Scrollbar height={effectiveHeight} thumb={thumb} />
      </Box>
    </Pane>
  );
});

/** The chat pane's history-scroll intents: `k` = older (window up), `j` = newer (window down). */
type ScrollIntent = 'scrollUp' | 'scrollDown';

/** A stable empty keymap for a blurred pane (so the `useMemo`/registration identity doesn't churn).
 * Typed `PanelKeymap<ScrollIntent>` so the `focused ? keymap : EMPTY_KEYMAP` ternary is one type. */
const EMPTY_KEYMAP: PanelKeymap<ScrollIntent> = { keymap: [], onIntent() {} };

// ---------------------------------------------------------------------------
// Stage
// ---------------------------------------------------------------------------

/**
 * The Stage region. Renders one focusable chat Pane per favorited crow tiled in the center, and — when
 * a document is open (`docView.open`) — a focusable {@link StageDocPane} beside them: to the RIGHT in
 * landscape, stacked BELOW in portrait (the orientation reflow the Rails use). Grows to fill whatever
 * the Rails leave.
 *
 * When the Stage has nothing to show (no favorited crows AND no open doc), it renders an empty
 * `flexGrow` box so the layout doesn't collapse oddly (the Rails keep their natural share and the Body
 * stays balanced) — chosen over `null` because returning `null` would let the Rails expand to fill the
 * freed center, which looks wrong on a wide terminal. The guard checks the doc too: a doc can be open
 * with no chat panes, and it must still appear.
 *
 * Rule 1: `React.memo` + narrow selectors (`shallow` on roster/conversations/favorites; the doc pane
 * reads its own `docView` body/status). The Stage subscribes to `docView.open` (the identity of the
 * open doc) so opening/closing a doc — or switching to a different one — re-renders + re-keys the pane.
 * Rule 2: `useOpenChatPanes` decides which chat panes exist (spec order; favorites default merged
 * with the explicit open/close overrides — item 9b).
 */
export const Stage = memo(function Stage({
  minCells,
  axis,
  paneGap = 0,
}: {
  /**
   * The Stage's guaranteed minimum cross-axis size in CELLS (R3/R4 — `≥ 60%` of the terminal),
   * computed by the layout-budget engine and threaded from App. Applied as `minWidth` (landscape) or
   * `minHeight` (portrait) so the Stage can never be starved below its floor even if a rail mis-sizes
   * (belt-and-suspenders with the rails' explicit, budget-bounded sizes). Defaults to 0 so the Stage
   * renders sanely if a caller omits it (e.g. a test mounting it bare).
   */
  readonly minCells?: number;
  /** Which dimension `minCells` floors: `'width'` in landscape, `'height'` in portrait. */
  readonly axis?: 'width' | 'height';
  /** The user-configured spaces between adjacent pane borders (the "Pane gap" setting, 0–4). Applied
   * between the chat panes (their row's `columnGap`) and between the chat group and the doc pane
   * (the outer box's `columnGap` in landscape, `rowGap` in portrait). Defaults to 0 (flush borders). */
  readonly paneGap?: number;
} = {}): JSX.Element {
  const floor = minCells ?? 0;
  const floorWidth = axis === 'width' ? floor : undefined;
  const floorHeight = axis === 'height' ? floor : undefined;
  const roster: RosterState = useAppStore((s) => s.roster, shallow);
  const conversations: ConversationsState = useAppStore((s) => s.conversations, shallow);
  const favorites: FavoritesState = useAppStore((s) => s.favorites, shallow);
  // The open doc's identity (or null) — subscribing here is what makes opening a doc re-render the
  // Stage and mount the doc pane. `shallow` so a body/status change inside the slice doesn't churn the
  // Stage (the doc pane reads those itself); only the `{kind,name}` identity flips this.
  const openDoc: OpenDoc | null = useAppStore((s) => s.docView.open, shallow);
  const orientation = useOrientation();

  // Open panes = favorites default merged with the explicit open/close overrides (item 9b). The
  // overrides map ref-swaps on every toggle, so the hook re-tiles when a pane opens/closes.
  const { panes } = useOpenChatPanes(roster, favorites, conversations.paneOverrides);

  if (panes.length === 0 && openDoc === null) {
    // Nothing on the Stage: an invisible spacer that holds the center open (see the doc above). It
    // still carries the budget floor so an empty Stage keeps its guaranteed ≥60% share.
    return (
      <Box
        flexGrow={1}
        flexBasis={0}
        minWidth={floorWidth}
        minHeight={floorHeight ?? 0}
        overflow="hidden"
      />
    );
  }

  // Landscape: doc sits in a column to the RIGHT of the chat row. Portrait: doc stacks BELOW in a row.
  // One flex axis flip, mirroring the Rail reflow — the chat panes always tile in a row among
  // themselves; only the doc's placement relative to them changes.
  const landscape = orientation === 'landscape';
  return (
    <Box
      flexDirection={landscape ? 'row' : 'column'}
      flexGrow={1}
      flexBasis={0}
      // The budget floor (R3/R4): the Stage can never be sized below its guaranteed ≥60% share.
      // `minHeight` defaults to 0 (the f26b77a clip discipline) when the axis is width, and vice versa.
      minWidth={floorWidth}
      minHeight={floorHeight ?? 0}
      overflow="hidden"
      // Inter-region gap between the chat group and the doc pane: a horizontal gap in landscape (they
      // sit side-by-side), a vertical gap in portrait (they stack). `0` = flush (the default).
      columnGap={landscape ? paneGap : 0}
      rowGap={landscape ? 0 : paneGap}
    >
      {/* Chat-history panes tile across the center, splitting the width evenly (each Pane flexGrow 1,
          so chat keeps the lion's share next to / above the doc). Rendered only when there ARE chat
          panes: an empty `flexGrow={1}` chat box would still claim half the Stage, leaving the doc at
          half width (an empty left half) when a doc is open with no favorited crows — so when panes
          is empty the doc pane (also `flexGrow={1}`) fills the Stage on its own. */}
      {panes.length > 0 && (
        <Box flexDirection="row" flexGrow={1} minHeight={0} overflow="hidden" columnGap={paneGap}>
          {panes.map((identity) => (
            <ChatPane key={identity.agentId} identity={identity} conversations={conversations} />
          ))}
        </Box>
      )}
      {/* The open document, a focusable Stage pane (focus id `stage:doc:<name>`). Keyed by the doc
          NAME so opening a different doc remounts it (resets scroll + re-registers its keymap). */}
      {openDoc !== null && <StageDocPane key={openDoc.name} open={openDoc} />}
    </Box>
  );
});
