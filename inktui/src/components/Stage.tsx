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
 * LEFT of the chat grid when the terminal is wide (landscape), stacking ABOVE it when narrow
 * (portrait) — documents are left/top-aligned and chats right/bottom-aligned (see
 * {@link ../layout/stageTiling.ts} for the split + grid rules). The doc pane slots into the SAME
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
import {
  type JSX,
  memo,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { shallow } from 'zustand/shallow';
import type { TmuxFrameEvent } from '../bus/protocol.js';
import { useAppStore } from '../hooks/useAppStore.js';
import { useBusClient } from '../hooks/useBusClient.js';
import { type GotoIntent, useGotoLine } from '../hooks/useGotoLine.js';
import {
  useBindings,
  useEffectiveFocus,
  useFocusRef,
  useMeasureFocus,
  usePanelKeymap,
  usePaneScrollBus,
} from '../hooks/useInputStores.js';
import { useOrientation } from '../hooks/useOrientation.js';
import { CHAT_FOCUS, type FocusId, type StagePaneId } from '../input/focusStore.js';
import type { PanelKeymap } from '../input/keymap.js';
import { computeStageLayout } from '../layout/stageTiling.js';
import type { AgentIdentity } from '../selectors/agentIdentity.js';
import {
  type ChatTurn,
  type TurnSpeaker,
  useActiveAgent,
  useConversationTurns,
  useOpenChatPanes,
} from '../selectors/conversationsSelectors.js';
import { harnessModelFooter, worktreeLabel } from '../selectors/harnessDisplay.js';
import type {
  ChatViewMode,
  ConversationsState,
} from '../store/conversations/conversationsSlice.js';
import type { OpenDoc } from '../store/docView/docViewSlice.js';
import type { FavoritesState } from '../store/favorites/favoritesSlice.js';
import type { RosterState } from '../store/roster/rosterSlice.js';
import { useTheme } from '../theme/themeStore.js';
import { type BlockKind, classifyBlocks } from '../transcript/blocks.js';
import { useBottomBarLines } from './BottomBar.js';
import { computeScrollThumb, StageDocPane } from './DocPane.js';
import { META_SEP } from './glyphs.js';
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

/** The crow's `harness ◇ model` bottom-LEFT border label, looked up from the roster by agent id.
 * `null` when the row is gone or neither part is known — the left footer then draws plain. Rule 2:
 * the wording lives in {@link ../selectors/harnessDisplay.js}; this just joins it to the roster row. */
function footerFor(roster: RosterState, agentId: string): string | null {
  const row = roster.rows.find((r) => r.agentId === agentId);
  if (row === undefined) {
    return null;
  }
  return harnessModelFooter(row.harness, row.model, META_SEP);
}

/** The crow's worktree bottom-RIGHT border label (the bare `.murder/worktrees/<name>` subdir, or
 * `main`). `null` only when the roster row is gone. Rule 2: the wording lives in
 * {@link ../selectors/harnessDisplay.js worktreeLabel}; this just looks up the row. */
function worktreeFor(roster: RosterState, agentId: string): string | null {
  const row = roster.rows.find((r) => r.agentId === agentId);
  if (row === undefined) {
    return null;
  }
  return worktreeLabel(row.worktreePath ?? null);
}

/** How many turns scroll past per `j`/`k` (the window step). */
const SCROLL_STEP = 1;
/** Fallback turn-window size before the grid has measured its height (first paint or sizeless test
 * render). Once the grid reports a real height the derived per-pane `contentHeight` drives the window. */
const FALLBACK_HEIGHT = 20;

/** "Stick to bottom" tolerance: while `scrollUp` (lines hidden below the window's bottom) is within
 * this many lines of the tail, the pane is considered AT the bottom — a newly-arriving message snaps
 * it to the newest line. Above this, the user is deliberately reading back, so new lines must NOT
 * yank the window down; their absolute position is preserved instead. */
const NEAR_BOTTOM_THRESHOLD = 3;

/** Rows of pane chrome between a pinned row height and the inner fill-box height: the inline-title
 * top border (1) + Ink's own bottom border on the content box (1). The footer overlay is net-0
 * (marginTop:-1 cancels its row), so chrome is exactly 2 — see Pane.tsx / paneBorder.tsx. */
const PANE_CHROME_ROWS = 2;

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

/**
 * Format one turn's faithful multi-line text into its physical display lines, classified into
 * {@link Block}s (TUIchat-2 readability engine). Each block's lines are emitted verbatim with ONE
 * blank line between blocks; a leading blank inside the turn body is dropped (the inter-turn separator
 * in {@link flattenTurns} owns the gap between turns). Pure; the legacy `›`/`·` inline marker is gone —
 * the speaker is now a left gutter color-bar drawn at render time (see {@link ChatPane}). Exported as
 * the test seam (returns the styled physical lines so block boundaries + rhythm are unit-testable).
 *
 * Vertical rhythm: blocks are separated by exactly one blank line and runs of >1 blank collapse to one
 * (the parser joins blocks with `\n\n`, so `classifyBlocks` already splits on blanks; we re-insert a
 * single blank between consecutive blocks rather than trusting the source's blank count).
 */
export function formatTurnLines(turn: ChatTurn): readonly ChatLine[] {
  const blocks = classifyBlocks(turn.text);
  const out: ChatLine[] = [];
  for (const block of blocks) {
    // Drop fully-blank/empty blocks (defensive — classifyBlocks shouldn't emit them) and skip a
    // block that is only whitespace, so the rhythm never accumulates empty rows.
    if (block.lines.length === 0) continue;
    if (out.length > 0) {
      out.push({ speaker: turn.speaker, kind: 'blank', text: '', firstOfTurn: false });
    }
    block.lines.forEach((text, i) => {
      out.push({
        speaker: turn.speaker,
        ...(turn.tone === undefined ? {} : { tone: turn.tone }),
        kind: block.kind,
        text,
        firstOfTurn: out.length === 0 || (i === 0 && out[out.length - 1]?.kind === 'blank'),
      });
    });
  }
  // A turn whose text was empty/whitespace-only collapses to nothing here; the caller's separator
  // logic then won't strand a lone blank.
  return out;
}

/** The theme color for a turn's speaker (user green, assistant body text, tool warning, …). The
 * speaker color paints the left gutter bar; prose/list body text stays default, code/pre is dimmed. */
function speakerColor(speaker: TurnSpeaker, theme: ReturnType<typeof useTheme>): string {
  switch (speaker) {
    case 'user':
      return theme.success;
    case 'assistant':
      return theme.text;
    case 'tool':
      return theme.warning;
    case 'plan':
      return theme.heading;
    case 'notice':
      return theme.error;
    default:
      return theme.muted;
  }
}

/** Summary turns are still assistant-speaker content, but need a distinct visual color from the
 * verbatim final answer shown after them in Condensed view. */
function chatLineColor(line: ChatLine, theme: ReturnType<typeof useTheme>): string {
  if (line.tone === 'summary') {
    return theme.accent;
  }
  return speakerColor(line.speaker, theme);
}

/** One physical line of chat history: a single text row carrying its source speaker (for the gutter
 * color) and its block kind (for styling — prose wraps, code/pre truncate as no-wrap islands, list
 * keeps its bullet structure). The chat pane windows over THESE, not over whole turns — see
 * {@link flattenTurns}. `blank` is a rhythm separator (between blocks and between turns). */
interface ChatLine {
  readonly speaker: TurnSpeaker;
  readonly tone?: ChatTurn['tone'];
  /** The block kind this line belongs to, or `blank` for a rhythm separator line. */
  readonly kind: BlockKind | 'blank';
  readonly text: string;
  /** True for the first content line of a turn (and the first line after an intra-turn blank) — the
   * gutter draws its cap glyph here so a turn reads as one block with a single bar head. */
  readonly firstOfTurn: boolean;
}

/**
 * Flatten ordered turns into the physical lines they render as — each turn classified into styled
 * blocks by {@link formatTurnLines}, with one BLANK separator line between consecutive turns (so
 * messages read as visually distinct blocks, not a solid run of rows). Consecutive assistant turns
 * are a visual exception: parsers can split one agent reply at blank-line gaps, so a run of adjacent
 * assistant JSON blocks is painted as one visual message. The gap stays, but only the first content
 * row in the run gets the solid head glyph; later assistant rows use continuation bars.
 *
 * This is the fix for dead scrolling on long chats: the pane must window by *line* (the unit it draws
 * and the unit `measureElement` would count), exactly as {@link ./DocPane.js StageDocPane} windows the
 * document body. Windowing by whole turns made `maxScrollUp = turns.length − height`, which is ≤ 0
 * whenever a few long multi-line turns fill the viewport — so `k`/`j` had nothing to move and the
 * history was stuck.
 *
 * Deterministic heights (the measure-wrap-trap mitigation): every block contributes EXACTLY
 * `block.lines.length` physical {@link ChatLine}s here — the scroll window, the scrollbar geometry,
 * and the visible slice all count these flat lines, never a measured height. Code/pre islands render
 * `wrap="truncate"` so one source line is one drawn row; prose lines may soft-wrap to >1 terminal row
 * but the window still advances one logical line per `j`/`k`, which is the existing (and tested)
 * contract for long prose turns. So the height math is pure data, immune to the
 * `measureElement`-lies-about-wrapped-content trap. The separator is a real ChatLine (not render-time
 * spacing) so the window/scroll math counts exactly what is drawn. Pure (no React); the test seam.
 */
export function flattenTurns(turns: readonly ChatTurn[]): readonly ChatLine[] {
  const lines: ChatLine[] = [];
  let previousRenderedTurn: ChatTurn | null = null;
  for (const turn of turns) {
    const turnLines = formatTurnLines(turn);
    if (turnLines.length === 0) continue;
    const continuesAssistantRun =
      previousRenderedTurn?.speaker === 'assistant' &&
      turn.speaker === 'assistant' &&
      previousRenderedTurn.tone === turn.tone;
    if (lines.length > 0) {
      lines.push({ speaker: turn.speaker, kind: 'blank', text: '', firstOfTurn: false });
    }
    for (const line of turnLines) {
      lines.push(
        continuesAssistantRun && line.kind !== 'blank' ? { ...line, firstOfTurn: false } : line,
      );
    }
    previousRenderedTurn = turn;
  }
  return lines;
}

/** The left gutter glyph: a solid bar at a turn's head, a lighter bar on continuation rows, so a turn
 * reads as one block with a single bar head (the speaker-gutter replacement for the old inline
 * `›`/`·` prefixes). A blank rhythm line draws no gutter. */
const GUTTER_HEAD = '▌';
const GUTTER_CONT = '▏';

/**
 * Render one physical {@link ChatLine} as a gutter + content ROW (TUIchat-2 speaker gutters + per-block
 * styling). The row is `flexShrink={0}` so Yoga never drops it (the skipped-line bug) and each source
 * line maps to a known number of rows for the deterministic window math:
 *  - **gutter** — a left color-bar in the speaker's theme color (solid head on the turn's first line,
 *    a lighter bar on continuations); `flexShrink={0}` + fixed width so it never wraps or collapses.
 *  - **prose / list** — default body text. prose WRAPS to the pane width (the only place wrapping
 *    happens — Ink's default `wrap`); list keeps its bullet structure (also wraps, but its leads carry
 *    the indent). Note a wrapped prose row spans >1 terminal row; the window still advances one logical
 *    line per `j`/`k` (the existing long-prose contract), so the height math stays pure data.
 *  - **code / pre** — a DIM, no-wrap island (`wrap="truncate"`): internal spaces + column alignment are
 *    preserved and a too-wide line is clipped (never re-wrapped into a zigzag). One source line is
 *    exactly one drawn row, so the deterministic height holds and `measureElement` is never consulted.
 *  - **blank** — a single space (a real row the rhythm/scroll math counts).
 */
function ChatHistoryLine({
  line,
  theme,
}: {
  readonly line: ChatLine;
  readonly theme: ReturnType<typeof useTheme>;
}): JSX.Element {
  const gutterColor = chatLineColor(line, theme);
  // A no-wrap island for verbatim regions; default wrapping for prose/list (the single wrap site).
  const verbatim = line.kind === 'code' || line.kind === 'pre';
  const content =
    line.kind === 'blank' ? (
      // A real space so the blank row occupies a line (the scroll math counts it).
      <Text> </Text>
    ) : verbatim ? (
      // Dim, no-wrap island. `flexShrink={0}` + truncate keeps internal spaces and clips overflow
      // instead of re-wrapping — alignment survives end to end.
      <Box flexShrink={0}>
        <Text dimColor wrap="truncate">
          {line.text === '' ? ' ' : line.text}
        </Text>
      </Box>
    ) : (
      // prose / list: default-wrapped body text in the speaker color.
      <Text color={gutterColor}>{line.text === '' ? ' ' : line.text}</Text>
    );
  return (
    <Box flexDirection="row" flexShrink={0}>
      {/* The speaker gutter: a fixed two-cell bar (glyph + trailing space), never shrinking so the
          content never overruns it. A blank rhythm line draws spaces (no bar) so gaps stay clean. */}
      <Box flexShrink={0} width={2}>
        {line.kind === 'blank' ? (
          <Text> </Text>
        ) : (
          <Text color={gutterColor}>{line.firstOfTurn ? GUTTER_HEAD : GUTTER_CONT} </Text>
        )}
      </Box>
      <Box flexGrow={1} minWidth={0} flexDirection="column">
        {content}
      </Box>
    </Box>
  );
}

/** Placeholder shown before the first tmux frame arrives for this crow's session. */
const TMUX_WAITING_TEXT = '[waiting for tmux frame…]';

/**
 * The inline tmux view (TUIchat-5) — the raw `tmux.frame` capture for one crow rendered INSIDE its
 * chat pane as the third per-pane view state, replacing the retired fullscreen modal. No fullscreen
 * takeover: it fills the pane's inner content box only.
 *
 * ## Subscription lifecycle (the scaling guard)
 * On mount it opens a bus subscription filtered to `{ type:'tmux.frame', agent_id }` and updates local
 * `frame` state per event; the `useEffect` cleanup closes it on unmount. Because this component is
 * mounted ONLY while the pane's view mode is `tmux` (the Stage seam renders it in that branch alone),
 * leaving tmux mode unmounts it and fires the cleanup — so the subscription closes when the pane
 * LEAVES tmux mode, not only when the pane is destroyed. That is the "close on mode-leave, no idle
 * streams" requirement: N visible tmux panes ⇒ exactly N live subscriptions, dropping to N−1 the
 * instant one cycles away. Rule-1 note: like the old `TmuxFrame`, this is the sanctioned narrow
 * exception to "no `useBusClient` in a component" — transient streaming display data, not a domain
 * slice (see {@link ../hooks/useBusClient.js}).
 *
 * ## Deterministic height + one-row-per-line (the measure-wrap-trap mitigation, BUG-2 fix)
 * The frame is a multi-line ANSI capture. It is rendered as ONE `<Text wrap="truncate">` ROW PER
 * FRAME LINE, capped to the integer `height` the pane already computed (its `effectiveHeight`, from
 * ChatGrid's integer row distribution — never `measureElement` on the wrapped/boxed frame). This is
 * deliberate, not stylistic: a SINGLE `<Text wrap="truncate">` fed the whole multi-line string
 * collapses the ENTIRE frame to one row the moment its first line overflows the pane width (Ink's
 * `truncate` cuts at the first wrap point and drops every line after it). That made a real full-width
 * capture render as a single clipped line — the pane looked empty/collapsed (BUG-2: the pane appeared
 * to vanish until an Alt+w reflow re-measured it). Splitting into per-line rows makes each captured
 * terminal line land on its own row 1:1, and the row count is pure data (`min(lines, height)`), immune
 * to the measure-wrap trap.
 *
 * Layout discipline: the outer box pins the integer `height` with `overflow:hidden` + `flexShrink={0}`
 * (a too-tall frame clips in-pane instead of pushing the layout) AND `width="100%"` + `minWidth={0}` so
 * it fills its cell but can NEVER demand more width than the cell — without `minWidth={0}` a flex ROW
 * item's default `min-width:auto` is its (wide) content min-size, which can starve a side-by-side
 * sibling pane. Each line row is `flexShrink={0}` so Yoga never drops it (the skipped-line bug).
 */
function TmuxFrameInline({
  agentId,
  height,
}: {
  readonly agentId: string;
  readonly height: number;
}): JSX.Element {
  const bus = useBusClient();
  const [frame, setFrame] = useState('');
  useEffect(() => {
    const unsubscribe = bus.subscribe(
      (event) => {
        if (event.type !== 'tmux.frame') return;
        const tmuxEvent: TmuxFrameEvent = event;
        setFrame(tmuxEvent.frame);
      },
      { type: 'tmux.frame', agent_id: agentId },
    );
    return unsubscribe;
  }, [bus, agentId]);
  // One row per captured line, deterministically capped to the pane's integer height (never measured).
  // The waiting placeholder is a single line; a real frame's lines are kept verbatim and clipped.
  const lines = (frame !== '' ? frame : TMUX_WAITING_TEXT)
    .split('\n')
    .slice(0, Math.max(height, 0));
  return (
    <Box
      flexDirection="column"
      flexShrink={0}
      width="100%"
      minWidth={0}
      height={height}
      overflow="hidden"
    >
      {lines.map((text, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: frame lines are position-keyed (row index is the stable identity for a captured grid line, mirroring the chat-history slice).
        <Box key={i} flexShrink={0}>
          <Text wrap="truncate">{text === '' ? ' ' : text}</Text>
        </Box>
      ))}
    </Box>
  );
}

/**
 * One crow's chat-history Pane — a focusable Stage pane. Owns its scroll window (`useState`, rule 1),
 * declares `j`/`k` to the keymap registry ONLY while focused (so exactly one chat pane's history
 * scroll is live, and a blurred pane never claims `j`), and flips the Pane's focus color when it holds
 * the effective focus. The Pane's outer box carries the focus ref so `useMeasureFocus` registers the
 * whole bordered region's rect for directional nav (matching the panel recipe in {@link ./Pane.tsx}).
 *
 * The scroll-window height arrives as the deterministic `contentHeight` PROP, computed by {@link
 * ChatGrid} from its measured grid height (see {@link paneContentHeights}). This pane used to
 * self-measure its (parent-controlled) fill box via `measureElement` in a `useLayoutEffect` — but the
 * pane is `React.memo`'d and the box height is set by the GRID's row sizing, not by any pane prop, so
 * when the grid re-pinned a row the memo'd pane never re-rendered, never re-ran its measure, and the
 * window went stale (blank lines below content, or clipped). Receiving the height as data makes the
 * window a pure function of the layout — and unit-testable.
 */
export const ChatPane = memo(function ChatPane({
  identity,
  conversations,
  chatTarget,
  footer,
  worktree,
  contentHeight,
}: {
  readonly identity: AgentIdentity;
  readonly conversations: ConversationsState;
  /** True when this pane's crow is the chat input's active send target — while the chat input holds
   * the effective focus, the targeted pane is highlighted too (so the user sees where a typed
   * message will land without moving focus off the input). */
  readonly chatTarget: boolean;
  /** The crow's `harness ◇ model` label for the bottom-LEFT border, or `null` when unknown (so the
   * left footer draws plain). Derived in the Stage from the roster row (rule 2). */
  readonly footer: string | null;
  /** The crow's worktree label for the bottom-RIGHT border (the bare `.murder/worktrees/<name>`
   * subdir, or `main`), or `null` when the row is gone. Derived in the Stage (rule 2). */
  readonly worktree: string | null;
  /** The inner fill-box height for this pane, computed deterministically by ChatGrid from the pinned
   * row height; `undefined` only before the grid's first measure. Replaces self-measuring a
   * parent-controlled height that went stale behind React.memo. */
  readonly contentHeight: number | undefined;
}): JSX.Element {
  const theme = useTheme();
  const focusId: FocusId = chatPaneFocusId(identity.agentId);

  // TUIchat-3 view-mode seam. Effective mode = per-pane override ?? the settings default.
  // TUIchat-4: `condensed` now transforms the block stream (rolling chunk summaries replace their
  // attributed blocks) BEFORE the Phase-2 renderer — see `selectConversationView`/`condenseBlocks`.
  // `verbose` is unchanged; `tmux` shows the inline frame elsewhere and never reaches this turns path.
  const defaultChatViewMode = useAppStore((s) => s.settings.defaultChatViewMode);
  const viewMode: ChatViewMode =
    conversations.paneViewModes[identity.agentId] ?? defaultChatViewMode;

  const turns = useConversationTurns(identity.agentId, conversations, viewMode);

  // Focus highlight + rect registration — the same recipe as every panel (rule 5), but with the
  // Stage-pane focus id. useMeasureFocus drops the rect on unmount → focus re-homes to chat.
  const ref = useFocusRef();
  const effectiveFocus = useEffectiveFocus();
  const focused = effectiveFocus === focusId;
  // The Pane highlight is broader than focus: the chat input's send TARGET also lights up while the
  // user is typing (chat holds the effective focus). The keymap registration below stays gated on
  // the REAL focus — a target-highlighted pane must not claim `j`/`k` from the text field.
  const highlighted = focused || (chatTarget && effectiveFocus === CHAT_FOCUS);
  useMeasureFocus(focusId, ref);

  // Local scroll offset (rule 1): how many turns are hidden ABOVE the window's top. 0 = pinned to the
  // newest turns (the bottom). Clamped to the available scroll range when rendering so a shrinking
  // transcript can't strand the window past its end.
  const [scrollUp, setScrollUp] = useState(0);

  // Window height arrives as a deterministic prop from ChatGrid (which alone measures the grid and
  // distributes integer row heights). Fallback covers first paint and sizeless test renders — when
  // the grid hasn't measured yet, `contentHeight` is undefined.
  const effectiveHeight = contentHeight ?? FALLBACK_HEIGHT;

  // Window by physical LINE (the unit drawn + measured), exactly as StageDocPane windows the document
  // body — NOT by whole turns. See flattenTurns for why turn-count windowing left long chats stuck.
  const lines = useMemo(() => flattenTurns(turns), [turns]);
  const maxScrollUp = Math.max(lines.length - effectiveHeight, 0);
  const clampedScroll = Math.min(scrollUp, maxScrollUp);

  // Stick-to-bottom on new content (BUG-10) WITHOUT yanking a reader off their place. The decision is
  // made against the PRE-update `scrollUp` captured in a ref, so it never races the same render that
  // grows `lines`: each render syncs the refs to the values it just used, and the effect (which runs
  // AFTER that sync) reads the values from the PREVIOUS committed render.
  //   • near the bottom before → snap to the tail (`setScrollUp(0)`) so the new reply is in view;
  //   • scrolled up           → bump the offset by the number of new lines so the exact lines the user
  //                             was reading stay stationary (the offset counts from the tail, which
  //                             just moved down by `delta`).
  // Negative `delta` (blocks collapse / re-summarize) doesn't snap; the render-time clamp + the
  // resize-clamp below keep `scrollUp` within the new bounds. First mount seeds the ref to the current
  // length so it can't snap spuriously.
  const prevLenRef = useRef<number | null>(null);
  // Snapshot of "was the user near the bottom?" as of the LAST render whose length had not yet grown.
  // It is refreshed only on a non-growth render (below), so on the render that delivers new lines the
  // effect reads the pre-growth decision — never the just-recomputed (post-growth) offset. This is how
  // we dodge the race: the deciding value is committed strictly before the length increase.
  const wasNearBottomRef = useRef(true);
  if (prevLenRef.current === null || lines.length <= prevLenRef.current) {
    wasNearBottomRef.current = clampedScroll <= NEAR_BOTTOM_THRESHOLD;
  }
  useEffect(() => {
    const prevLen = prevLenRef.current;
    prevLenRef.current = lines.length;
    if (prevLen === null) {
      return; // first mount: no prior length to diff against — don't snap.
    }
    const delta = lines.length - prevLen;
    if (delta <= 0) {
      // Shrink (or no growth). Just keep the offset valid against the new max; never snap.
      setScrollUp((s) => Math.min(s, maxScrollUp));
      return;
    }
    if (wasNearBottomRef.current) {
      setScrollUp(0); // stick to the tail → the new reply scrolls into view.
    } else {
      setScrollUp((s) => Math.min(s + delta, maxScrollUp)); // preserve absolute position.
    }
  }, [lines.length, maxScrollUp]);

  // `g<digits>` go-to-line (the shared gesture — see useGotoLine): the 1-based history line lands at
  // the TOP of the window. `scrollUp` counts hidden lines from the tail, so line N maps to
  // `maxScrollUp − (N − 1)`, clamped to the scroll range.
  const jump = useCallback(
    (line: number) => setScrollUp(Math.min(Math.max(maxScrollUp - (line - 1), 0), maxScrollUp)),
    [maxScrollUp],
  );
  const goto = useGotoLine(jump);

  // History-scroll keymap (rule 5: declared, not handled). `j`/`k` move the window; `alt+j`/`alt+k`
  // are the global directional-nav layer (pane-to-pane), so they never reach here. Registered only
  // while focused — the registry then holds at most one chat-pane keymap. Memoised on the scroll
  // bounds so the handler closes over a fresh `maxScrollUp` without re-registering every render.
  // The goto entries are spread FIRST so a live `g` capture's digits win over any pane chord.
  const keymap: PanelKeymap<ScrollIntent | GotoIntent> = useMemo(
    () => ({
      keymap: [
        ...goto.entries,
        {
          chord: [{ input: 'k' }, { key: { upArrow: true } }],
          intent: 'scrollUp',
          description: 'older',
        },
        {
          chord: [{ input: 'j' }, { key: { downArrow: true } }],
          intent: 'scrollDown',
          description: 'newer',
        },
      ],
      onIntent(intent) {
        // Goto intents are consumed by the gesture; any other intent ends a live capture and then
        // acts with its normal meaning (the useGotoLine contract).
        if (goto.handle(intent)) {
          return;
        }
        goto.clear();
        if (intent === 'scrollUp') {
          setScrollUp((s) => Math.min(s + SCROLL_STEP, maxScrollUp));
        } else {
          setScrollUp((s) => Math.max(s - SCROLL_STEP, 0));
        }
      },
    }),
    [maxScrollUp, goto],
  );
  // Register only while focused so a blurred pane doesn't own `j`/`k` (no-op keymap otherwise). An
  // empty keymap when blurred means the registry entry exists but matches nothing — the dispatcher
  // only consults the FOCUSED id's entry anyway, so this is belt-and-suspenders for clarity.
  usePanelKeymap(focusId, focused ? keymap : EMPTY_KEYMAP);

  // Mouse-wheel scroll (the scroll-bus subscription). Subscribed UNCONDITIONALLY — not gated on
  // `focused` like the keymap — because the wheel can target this pane while the chat INPUT holds
  // focus (scrolling the input's active-target history). `up` reveals older turns (scrollUp counts
  // hidden lines from the tail, so it grows); `down` reveals newer. The bound is read from a ref so
  // the subscription installs once per focus id, not on every `maxScrollUp` change.
  const paneScroll = usePaneScrollBus();
  const maxScrollUpRef = useRef(maxScrollUp);
  maxScrollUpRef.current = maxScrollUp;
  useEffect(
    () =>
      paneScroll.subscribe(focusId, (direction, amount) => {
        setScrollUp((s) =>
          direction === 'up'
            ? Math.min(s + amount, maxScrollUpRef.current)
            : Math.max(s - amount, 0),
        );
      }),
    [paneScroll, focusId],
  );

  // The visible window: the effectiveHeight newest LINES shifted up by the (clamped) scroll offset.
  // Slice arithmetic keeps the most recent lines by default (scroll 0 → pinned to the tail/newest).
  const end = lines.length - clampedScroll;
  const start = Math.max(end - effectiveHeight, 0);
  const visibleLines = lines.slice(start, end);
  const thumb = computeScrollThumb(lines.length, start, effectiveHeight);

  return (
    // The right border doubles as the scroll track (the Pane's `scrollbar` prop) — no separate
    // scrollbar column, so the content keeps the default right gutter.
    <Pane
      ref={ref}
      title={identity.label}
      focused={highlighted}
      titleExtra={
        <>
          {/* A space precedes the bracket so the title reads `name [rogue]`, not `name[rogue]`. */}
          <Text dimColor>{` [${kindLabel(identity.kind)}]`}</Text>
          {/* Live `g<digits>` capture indicator — shows the line number as it is typed. */}
          {goto.pending !== null && <Text color={theme.warning}>{` g${goto.pending}`}</Text>}
        </>
      }
      scrollbar={{ height: effectiveHeight, thumb }}
      // The harness ◇ model label rides the bottom-LEFT border; the crow's worktree rides the
      // bottom-RIGHT (mirroring the top-left name, right-aligned to the right edge).
      footerLeft={footer !== null ? <Text dimColor>{footer}</Text> : undefined}
      footerRight={worktree !== null ? <Text dimColor>{worktree}</Text> : undefined}
    >
      {/* Fill box: sizes to the Pane's inner content area (flexGrow + overflow hidden). Its height is
          received as `contentHeight` (ChatGrid measures + distributes), not self-measured here. */}
      <Box flexDirection="column" flexGrow={1} minHeight={0} overflow="hidden">
        {viewMode === 'tmux' ? (
          // TUIchat-5: the inline tmux frame, constrained to this pane's inner rect (no fullscreen
          // takeover). Keyed by the focus id so it remounts per crow; the subscription closes when
          // the pane leaves tmux mode (it unmounts here) — see TmuxFrameInline.
          <TmuxFrameInline agentId={identity.agentId} height={effectiveHeight} />
        ) : visibleLines.length === 0 ? (
          // verbose AND condensed route through the TUIchat-2 readability renderer (below). condensed
          // has no backend until TUIchat-4, so it renders identically to verbose for now.
          <Text dimColor>no history</Text>
        ) : (
          visibleLines.map((line, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: history lines are position-keyed (the windowed index is the stable identity for the visible slice, mirroring StageDocPane).
            <ChatHistoryLine key={start + i} line={line} theme={theme} />
          ))
        )}
      </Box>
    </Pane>
  );
});

/** The chat pane's history-scroll intents: `k` = older (window up), `j` = newer (window down). */
type ScrollIntent = 'scrollUp' | 'scrollDown';

/** A stable empty keymap for a blurred pane (so the `useMemo`/registration identity doesn't churn).
 * Typed to the pane's full intent union so the `focused ? keymap : EMPTY_KEYMAP` ternary is one type. */
const EMPTY_KEYMAP: PanelKeymap<ScrollIntent | GotoIntent> = { keymap: [], onIntent() {} };

/**
 * The inner fill-box height each pane in row `i` gets, derived deterministically from the grid's
 * measured height. Mirrors the integer row-height distribution exactly (`base`, with the first
 * `remainder` rows getting one extra cell) then subtracts the fixed pane chrome. Returns `undefined`
 * per row before the grid has measured (height 0) — the pane then falls back to FALLBACK_HEIGHT for
 * the first paint. This replaces ChatPane self-measuring its (parent-controlled) box height: a memo'd
 * pane never re-ran its measure when the grid re-pinned a row, stranding the window stale. Passing the
 * height down as data makes the window a pure function of the layout (and unit-testable). Pure; the
 * test seam.
 *
 * Uses the raw measured grid height — NOT the `staleByFooter`-zeroed value the row sizing uses — so a
 * BottomBar line-count change doesn't collapse the pane windows. (A transiently-too-large window can
 * never overflow the terminal: the fill box is `overflow:hidden`, so it clips in-pane; only ROW
 * pinning needs the staleByFooter overflow guard, which is left untouched.)
 */
export function paneContentHeights(
  measuredHeight: number,
  rowCount: number,
  paneGap: number,
): readonly (number | undefined)[] {
  if (rowCount <= 0) return [];
  if (measuredHeight <= 0) return Array.from({ length: rowCount }, () => undefined);
  const gaps = paneGap * Math.max(0, rowCount - 1);
  const avail = Math.max(0, measuredHeight - gaps);
  const base = Math.floor(avail / rowCount);
  const remainder = avail - base * rowCount;
  return Array.from({ length: rowCount }, (_, i) =>
    Math.max(base + (i < remainder ? 1 : 0) - PANE_CHROME_ROWS, 0),
  );
}

/**
 * The chat-history grid — one cross-axis line per `rows` entry, panes splitting each line.
 *
 * Why this measures its own height and assigns INTEGER row heights (instead of `flexGrow` rows):
 * a chat pane wears a bottom-border footer ({@link ./Pane.js Pane}'s `footerRight`), and that footer
 * — like any node positioned at a pane's bottom edge — drops its text when the pane lands on a
 * fractional (half-cell) height, which is exactly what `flexGrow` rows produce when an odd grid height
 * splits across ≥2 rows (Ink's own border survives as border-box space, but a footer overlay's
 * position rounds independently and clips). Measuring the grid's height once and handing each row an
 * explicit integer height (remainder spread across the first rows) means every pane gets a whole-cell
 * box, so the footer text is always drawn. Before the first measure (height 0) we fall back to
 * `flexGrow` so the first paint is still sane. The container itself stays `flexGrow` (its height is
 * parent-driven); only the ROWS are pinned — so there is no measure→resize feedback loop.
 */
function ChatGrid({
  rows,
  chatWeight,
  paneGap,
  conversations,
  roster,
  targetAgentId,
}: {
  readonly rows: readonly (readonly AgentIdentity[])[];
  readonly chatWeight: number;
  readonly paneGap: number;
  readonly conversations: ConversationsState;
  readonly roster: RosterState;
  readonly targetAgentId: string | null;
}): JSX.Element {
  const ref = useRef<DOMElement | null>(null);
  const [measuredHeight, setMeasuredHeight] = useState(0);
  // ## The stale-height latch (the landscape Stage doc-overflow fix)
  // This grid measures its own (`flexGrow`, slot-bounded) height and assigns each row a FIXED
  // `height={rowHeight}` — but `measuredHeight` is ONE RENDER STALE. On a no-resize Body SHRINK the
  // stale-too-large value makes the fixed rows overflow the now-shorter slot; the overflow propagates
  // past the Body's `height={rows}` clip and the terminal scrolls the TopBar into scrollback. Because a
  // terminal scroll is IRRECOVERABLE (Ink only erases `rows` lines, so the lost top line never
  // repaints) it is not enough to settle to the right height EVENTUALLY — not even one frame may exceed
  // the slot. The trigger is the BottomBar hint growing a row when a Stage doc is opened/focused (its
  // scroll keybinds), which shrinks the Body with no resize event. Fix, in layers:
  //  (1) When the BottomBar line count CHANGES we drop back to the pre-measure `flexGrow` row sizing
  //      THIS SAME RENDER (the `staleByFooter` branch below sets the effective height to 0 during
  //      render, the canonical "derive state from changed input while rendering" pattern). `flexGrow`
  //      rows can never overflow their slot, so the shrink frame is emitted at the correct height — no
  //      transient over-tall frame, no scroll. `seen` is reconciled in the effect, then the real
  //      measurement resumes and the integer distribution settles.
  //  (2) The rows are also `flexShrink={1}` (below) as belt-and-suspenders: any other stale-too-tall
  //      total CLIPS to the slot instead of forcing it taller.
  // (The doc region needs neither: it is pure `flexGrow` with no fixed-height child, so it shrinks
  // cleanly — which is why only the chat grid latched.)
  const footerLineCount = useBottomBarLines().length;
  const [seenFooterLineCount, setSeenFooterLineCount] = useState(footerLineCount);
  const staleByFooter = footerLineCount !== seenFooterLineCount;
  useLayoutEffect(() => {
    if (ref.current === null) return;
    if (staleByFooter) {
      setSeenFooterLineCount(footerLineCount);
      setMeasuredHeight(0);
      return;
    }
    const { height } = measureElement(ref.current);
    if (height !== measuredHeight) setMeasuredHeight(height);
  });
  // The height that actually drives the row sizing: 0 (→ `flexGrow` fallback) on the render where the
  // BottomBar count just changed, so the shrink frame can't overflow; the measured value otherwise.
  const effectiveHeight = staleByFooter ? 0 : measuredHeight;
  // Distribute the measured height into integer per-row heights summing to exactly the available
  // space (so the rows neither overflow nor leave a gap): `base` each, with the first `remainder`
  // rows getting one extra cell. The rowGaps are subtracted out first.
  const n = rows.length;
  const gaps = paneGap * Math.max(0, n - 1);
  const avail = Math.max(0, effectiveHeight - gaps);
  const base = n > 0 ? Math.floor(avail / n) : 0;
  const remainder = avail - base * n;
  // The per-row inner fill-box height handed down to each ChatPane — derived from the RAW measured
  // height (not the staleByFooter-zeroed `effectiveHeight`) so a BottomBar line-count change collapses
  // only the ROW pinning, never the pane scroll windows. See paneContentHeights.
  const contentHeights = paneContentHeights(measuredHeight, n, paneGap);
  return (
    <Box
      ref={ref}
      flexGrow={chatWeight}
      flexBasis={0}
      minWidth={0}
      minHeight={0}
      overflow="hidden"
      flexDirection="column"
      rowGap={paneGap}
    >
      {rows.map((row, i) => {
        // Pinned integer height once measured; `flexGrow` fallback before the first measure AND on the
        // render where the BottomBar count just changed (`effectiveHeight === 0`), so a Body shrink
        // never emits an over-tall frame (see the stale-height-latch note above).
        const rowHeight = effectiveHeight > 0 ? base + (i < remainder ? 1 : 0) : undefined;
        const sizing =
          rowHeight !== undefined
            ? { height: rowHeight, flexShrink: 1 }
            : { flexGrow: 1, flexBasis: 0 };
        return (
          // Row key = its agent-id membership join. The inner panes key by stable agentId (below),
          // so a pane that stays in the same row keeps its ChatPane scroll offset. But re-gridding
          // that moves an agent to a different row changes that row's join-key, remounting the row
          // and resetting the local scroll state of every pane in it — accepted for v0 (favoriting
          // reshuffles rows rarely and a reset-to-bottom there is tolerable).
          <Box
            key={row.map((identity) => identity.agentId).join(',')}
            flexDirection="row"
            {...sizing}
            minWidth={0}
            minHeight={0}
            overflow="hidden"
            columnGap={paneGap}
          >
            {row.map((identity) => (
              <Box
                key={identity.agentId}
                flexGrow={1}
                flexBasis={0}
                minWidth={0}
                minHeight={0}
                overflow="hidden"
                flexDirection="column"
              >
                <ChatPane
                  identity={identity}
                  conversations={conversations}
                  chatTarget={targetAgentId === identity.agentId}
                  footer={footerFor(roster, identity.agentId)}
                  worktree={worktreeFor(roster, identity.agentId)}
                  contentHeight={contentHeights[i]}
                />
              </Box>
            ))}
          </Box>
        );
      })}
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Stage
// ---------------------------------------------------------------------------

/**
 * The Stage region. Renders one focusable chat Pane per favorited crow tiled in a grid, and — when a
 * document is open (`docView.open`) — a focusable {@link StageDocPane} beside them: to the LEFT in
 * landscape, stacked ABOVE in portrait (documents left/top-aligned, chats right/bottom-aligned). The
 * doc/chat split and the chat grid shape are the pure {@link ../layout/stageTiling.ts computeStageLayout}.
 * Grows to fill whatever the Rails leave.
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
  const bindings = useBindings();

  // Open panes = favorites default merged with the explicit open/close overrides (item 9b). The
  // overrides map ref-swaps on every toggle, so the hook re-tiles when a pane opens/closes.
  const { panes } = useOpenChatPanes(roster, favorites, conversations.paneOverrides);
  // The chat input's active send target (the SAME resolution ChatInput displays on its border) — the
  // targeted pane is highlighted while the chat input holds focus, so the user sees where a typed
  // message will land. Resolved once here (not per-pane) and passed down as a boolean.
  const target = useActiveAgent(conversations, roster, favorites);

  if (panes.length === 0 && openDoc === null) {
    // Nothing on the Stage: a centered first-run hint instead of a void, in the same spacer that
    // holds the center open. It still carries the budget floor so an empty Stage keeps its
    // guaranteed ≥60% share. Labels come from the live bindings table so a modifier change (alt ⇄
    // ctrl) keeps the hint truthful.
    return (
      <Box
        flexGrow={1}
        flexBasis={0}
        minWidth={floorWidth}
        minHeight={floorHeight ?? 0}
        overflow="hidden"
        flexDirection="column"
        justifyContent="center"
        alignItems="center"
      >
        <Text dimColor>{`${bindings.label('global.spawn')} spawn a crow`}</Text>
        <Text
          dimColor
        >{`${bindings.label('panel.star')} star one in the crows panel to open its chat here`}</Text>
      </Box>
    );
  }

  // The arrangement (region weights + chat grid rows) is the pure {@link computeStageLayout}. Landscape
  // lays the doc region LEFT of the chat grid (a `row`); portrait stacks the doc ABOVE it (a `column`)
  // — the same orientation flip the Rails use, with documents left/top-aligned and chats right/bottom.
  const landscape = orientation === 'landscape';
  const { docWeight, chatWeight, rows } = computeStageLayout(panes, openDoc !== null, orientation);
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
      {/* Documents region — LEFT in landscape, TOP in portrait. A weighted cell (`flexBasis={0}` so its
          size is purely weight-driven, never content- or mount-order-driven). Rendered only when a doc
          is open; otherwise the chat region fills the Stage on its own. */}
      {openDoc !== null && (
        <Box
          flexGrow={docWeight}
          flexBasis={0}
          minWidth={0}
          minHeight={0}
          overflow="hidden"
          flexDirection="column"
        >
          {/* Keyed by the doc NAME so opening a different doc remounts it (resets scroll + re-registers
              its keymap under the new `stage:doc:<name>` focus id). */}
          <StageDocPane key={openDoc.name} open={openDoc} />
        </Box>
      )}
      {/* Chat-history region — RIGHT in landscape, BOTTOM in portrait. A grid: one cross-axis line per
          `rows` entry, each cell `flexBasis={0}` so the panes split their line evenly regardless of
          content width or the order crows were favorited (the old single-row tiling produced skinny,
          order-dependent columns — see {@link ../layout/stageTiling.ts}). The user's `paneGap` spaces
          the grid: a `rowGap` between stacked grid lines, a `columnGap` between side-by-side panes in
          a line (0 = flush borders, the default). */}
      {rows.length > 0 && (
        <ChatGrid
          rows={rows}
          chatWeight={chatWeight}
          paneGap={paneGap}
          conversations={conversations}
          roster={roster}
          targetAgentId={target?.agentId ?? null}
        />
      )}
    </Box>
  );
});
