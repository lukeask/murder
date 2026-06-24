/**
 * Conversations view-models — display-ready chat history per agent.
 *
 * Rule 2 in action: ALL formatting of raw blocks into display turns lives here, never in the store
 * or in components. The `ConversationsState` stores raw `ConversationBlock`s; this selector
 * produces ordered `ChatTurn[]` arrays ready to paint.
 *
 * Block→turn formatting mirrors the Python Textual `_segment_text` (`crows_view.py:464-544`):
 * user, assistant, tool_call, plan_update, agent_event, choice_prompt, notice. The
 * choice_prompt branch also carries the live-prompt trailing-segment heuristic
 * (`crows_view.py:595-608`) via `ChatTurn.isLivePrompt` — see that field's doc.
 * Unknown block types are passed through with a fallback label so new service events
 * don't silently vanish.
 *
 * Two layers (mirrors `crowsSelectors.ts`):
 *  - Pure transforms (`selectConversationTurns`) — no React, unit-testable in isolation.
 *  - `useConversationTurns` hook — component-facing, memoises on the agent's transcript identity.
 *
 * Per-agent favorited view (`selectFavoritesChatPanes`) — derives the ordered list of favorited
 * crow identities whose chat panes should be shown. Collaborator + rogue crows are default-
 * favorited (see `agentIdentity.isDefaultFavorited`). C11 owns the full prefs persistence.
 */

import { useMemo } from 'react';
import type {
  ChatViewMode,
  ChunkSummary,
  ConversationBlock,
  ConversationMeta,
  ConversationsState,
} from '../store/conversations/conversationsSlice.js';
import type { FavoritesState } from '../store/favorites/favoritesSlice.js';
import type { RosterState } from '../store/roster/rosterSlice.js';
import { type AgentIdentity, deriveAgentIdentity, isDefaultFavorited } from './agentIdentity.js';
import { isFavorited } from './favoritesSelectors.js';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/** The speaker of a chat turn. */
export type TurnSpeaker =
  | 'user'
  | 'assistant'
  | 'tool'
  | 'plan'
  | 'agent'
  | 'prompt'
  | 'notice'
  | 'unknown';

/** A single display-ready chat turn. */
export interface ChatTurn {
  /** Which agent/role spoke (for coloring). */
  readonly speaker: TurnSpeaker;
  /** Optional subtype for visual treatment without changing the speaker contract. */
  readonly tone?: 'summary';
  /** Display text (may be multiline). */
  readonly text: string;
  /** The originating block's id, if any (for keying in React lists). */
  readonly blockId: string | null;
  /**
   * True only for an unanswered `choice_prompt` that is the trailing block of the transcript —
   * i.e. a still-open live multiple-choice dialog the user can answer. Ports the Textual
   * `_live_choice_prompt` trailing-segment heuristic (`crows_view.py:595-608`): a live wizard is
   * always the last segment (the grammar appends it last while the pane shows it, and marks it
   * `answered` once it's gone), so liveness is "unanswered AND trailing", not a `live_state` field.
   *
   * The turn *text* is identical whether live or finalized (Textual's `_segment_text` keys the
   * answered/unanswered branch on `answered`, not position) — this flag is the only thing the
   * heuristic adds at this layer, letting a future wizard component swap on it. Absent/false on
   * every other turn.
   */
  readonly isLivePrompt?: boolean;
}

/** Chat history view-model for one agent. */
export interface ConversationView {
  readonly agentId: string;
  readonly turns: readonly ChatTurn[];
  /** True when this agent has at least one block. */
  readonly hasContent: boolean;
}

/** The ordered list of favorited crow identities whose panes should be rendered. */
export interface FavoritesChatPanesView {
  readonly panes: readonly AgentIdentity[];
}

/** The ordered list of crow identities whose chat panes are currently OPEN — the favorites default
 * merged with the explicit `paneOverrides` (item 9b). The Stage tiles exactly these. */
export interface OpenChatPanesView {
  readonly panes: readonly AgentIdentity[];
}

// ---------------------------------------------------------------------------
// Block→turn formatting (rule 2: presentation here, not in the store)
// ---------------------------------------------------------------------------

/**
 * Read a string field from an opaque `Record<string, unknown>`. Returns an empty string when the
 * field is absent or not a string. Using a helper avoids both `useLiteralKeys` (Biome prefers
 * dot-notation on Records) AND `noPropertyAccessFromIndexSignature` (tsconfig requires bracket
 * notation on index-signature types) — two rules that conflict on `Record<string, unknown>`.
 * The helper is the single resolution point: a typed call with a string-literal key.
 */
function str(obj: Readonly<Record<string, unknown>>, key: string): string {
  const v = obj[key];
  return typeof v === 'string' ? v : '';
}

/**
 * Read an unknown field from an opaque `Record<string, unknown>`. For non-string accesses.
 */
function field(obj: Readonly<Record<string, unknown>>, key: string): unknown {
  return obj[key];
}

function formatBlock(block: ConversationBlock): ChatTurn | null {
  const raw = block.raw;
  const blockId = block.id ?? null;

  switch (block.type) {
    // TUIchat-2 selector pass-through: the parser (Phase 1) emits a FAITHFUL multi-line `text` (real
    // newlines, verbatim code/tables, prose de-wrapped). We pass it straight to the renderer — only an
    // outer `.trim()` (strips leading/trailing blank lines/spaces; internal newlines + alignment are
    // untouched). NO pre-split / newline normalization here; the Stage's `classifyBlocks` does the
    // presentation-time grouping, so the multi-line structure must reach it intact.
    case 'user': {
      const text = str(raw, 'text').trim();
      if (!text) return null;
      return { speaker: 'user', text, blockId };
    }
    case 'assistant': {
      const text = str(raw, 'text').trim();
      if (!text) return null;
      return { speaker: 'assistant', text, blockId };
    }
    case CONDENSED_SUMMARY_TYPE: {
      // Synthetic Condensed-view block (TUIchat-4): the rolling chunk summary that stands in for a run
      // of intermediate blocks. Rendered as assistant-speaker prose through the Phase-2 engine, with a
      // distinct tone so the TUI can color summaries differently from the verbatim final answer.
      const text = str(raw, 'text').trim();
      if (!text) return null;
      return { speaker: 'assistant', tone: 'summary', text, blockId };
    }
    case 'tool_call': {
      const title = str(raw, 'title').trim();
      if (!title) return null;
      const parts: string[] = [title];
      const toolInput = field(raw, 'input');
      if (typeof toolInput === 'string' && toolInput.trim()) {
        parts.push(`$ ${toolInput.trim()}`);
      }
      const result = field(raw, 'result');
      if (typeof result === 'string' && result.trim()) {
        parts.push(result.trim());
      }
      if (field(raw, 'elided') === true) parts.push('[collapsed]');
      return { speaker: 'tool', text: parts.join('\n'), blockId };
    }
    case 'plan_update': {
      const title = str(raw, 'title').trim();
      const items = field(raw, 'items');
      if (!title || !Array.isArray(items)) return null;
      const lines: string[] = [title];
      for (const item of items) {
        if (item === null || typeof item !== 'object' || Array.isArray(item)) continue;
        const itemRec = item as Readonly<Record<string, unknown>>;
        const marker = field(itemRec, 'done') === true ? 'x' : ' ';
        const itemText = str(itemRec, 'text').trim();
        if (itemText) lines.push(`[${marker}] ${itemText}`);
      }
      return { speaker: 'plan', text: lines.join('\n'), blockId };
    }
    case 'agent_event': {
      // status · name · elapsed (status first), dropping empties; null if all empty.
      const parts = [
        str(raw, 'status').trim(),
        str(raw, 'name').trim(),
        str(raw, 'elapsed').trim(),
      ];
      const present = parts.filter((p) => p);
      if (present.length === 0) return null;
      return { speaker: 'agent', text: present.join(' · '), blockId };
    }
    case 'choice_prompt': {
      const question = str(raw, 'question').trim();
      if (!question) return null;
      const options = field(raw, 'options');
      const lines: string[] = [question];
      if (field(raw, 'answered') === true) {
        // Finalized: show only the chosen option(s). Single-select `chosen` is a number;
        // multi-select (CC AskUserQuestion multiSelect) `chosen` is the checked-number array.
        const chosen = field(raw, 'chosen');
        const chosenSet = new Set(Array.isArray(chosen) ? chosen : [chosen]);
        if (Array.isArray(options)) {
          for (const option of options) {
            if (option === null || typeof option !== 'object' || Array.isArray(option)) continue;
            const optRec = option as Readonly<Record<string, unknown>>;
            const number = field(optRec, 'number');
            if (!chosenSet.has(number)) continue;
            const label = str(optRec, 'label').trim();
            if (label) lines.push(`selected: ${String(number)}. ${label}`);
          }
        }
        return { speaker: 'prompt', text: lines.join('\n'), blockId };
      }
      // Unanswered: list every numbered option ("N. label"; multi-select shows checkboxes).
      if (Array.isArray(options)) {
        for (const option of options) {
          if (option === null || typeof option !== 'object' || Array.isArray(option)) continue;
          const optRec = option as Readonly<Record<string, unknown>>;
          const number = field(optRec, 'number');
          const label = str(optRec, 'label').trim();
          const checked = field(optRec, 'checked');
          const box = typeof checked === 'boolean' ? (checked ? '[✔] ' : '[ ] ') : '';
          if (label) lines.push(`${String(number)}. ${box}${label}`);
        }
      }
      return { speaker: 'prompt', text: lines.join('\n'), blockId };
    }
    case 'notice': {
      const rawMsg = str(raw, 'message').trim() || str(raw, 'text').trim();
      if (!rawMsg) return null;
      const severity = str(raw, 'severity').trim();
      const text = severity ? `${severity}: ${rawMsg}` : rawMsg;
      return { speaker: 'notice', text, blockId };
    }
    default: {
      // Unknown block type — pass through with a fallback label so new events don't vanish.
      return { speaker: 'unknown', text: `[${block.type}]`, blockId };
    }
  }
}

// ---------------------------------------------------------------------------
// Condensed view (TUIchat-4): attribution-driven block replacement
// ---------------------------------------------------------------------------

/**
 * The synthetic block `type` for a rolling chunk summary in Condensed view. `formatBlock` renders it
 * as an `assistant`-speaker turn (the summary is condensed assistant/tool intermediate output), so it
 * flows through the same Phase-2 renderer as real assistant prose. Distinct from any real wire type so
 * it can never collide with a parsed segment.
 */
const CONDENSED_SUMMARY_TYPE = '__condensed_summary__';

/**
 * Build the Condensed-view block stream for one agent (TUIchat-4): replace each chunk summary's
 * attributed run of blocks with a single synthetic summary block, leaving everything else as-is.
 *
 * Attribution semantics (TUIchatpaneupgrade Phase 4, Scope-decisions #3):
 *  - Each `ChunkSummary.blockIds` lists the EXACT block row-ids the `summary` stands in for. We drop
 *    every block whose id ∈ any summary's blockIds and emit the summary text in its place, anchored at
 *    the position of the earliest covered block (so summaries land in conversation order — they are
 *    already ordered by `chunkIdx`, and blocks are in ordinal order).
 *  - Blocks NOT covered by any summary (the still-buffering tail of intermediates below the char
 *    threshold) render as-is — Condensed degrades gracefully to verbose-like for them.
 *  - The FINAL reply (`assistant_final`, surfaced here as a block the backend never attributes to a
 *    summary) is never in any blockIds, so it always survives verbatim. This function additionally
 *    NEVER drops a block that isn't explicitly attributed, so even a defensive mis-attribution can't
 *    swallow the final.
 *
 * Pure. When `summaries` is empty (none fired yet / summarizer returned empty) it returns `blocks`
 * UNCHANGED (same identity) — Condensed shows the intermediates as-is, never blank.
 *
 * Block ids are compared as STRINGS: `ConversationBlock.id` is the stringified row id, while
 * `ChunkSummary.blockIds` are numeric; we stringify the latter to match.
 */
export function condenseBlocks(
  blocks: readonly ConversationBlock[] | undefined,
  summaries: readonly ChunkSummary[] | undefined,
): readonly ConversationBlock[] | undefined {
  if (!blocks || blocks.length === 0) return blocks;
  if (!summaries || summaries.length === 0) return blocks;

  // Map every covered block id → the summary that owns it (string keys to match ConversationBlock.id).
  // A block id appearing in two summaries (shouldn't happen — attribution is a partition) resolves to
  // the LAST writer; harmless since we anchor on first-seen position regardless.
  const protectedTailIds = trailingFinalAssistantRunIds(blocks);
  const summaryByBlockId = new Map<string, ChunkSummary>();
  for (const s of summaries) {
    for (const id of s.blockIds) {
      const key = String(id);
      if (!protectedTailIds.has(key)) {
        summaryByBlockId.set(key, s);
      }
    }
  }
  if (summaryByBlockId.size === 0) return blocks;

  const out: ConversationBlock[] = [];
  const emitted = new Set<ChunkSummary>();
  for (const block of blocks) {
    const id = block.id ?? null;
    const owner = id === null ? undefined : summaryByBlockId.get(id);
    if (owner === undefined) {
      // Uncovered block (incl. the never-attributed assistant_final) → verbatim.
      out.push(block);
      continue;
    }
    // Covered block: emit the owning summary ONCE, at the earliest covered position, then drop the
    // rest of that run.
    if (!emitted.has(owner)) {
      emitted.add(owner);
      out.push({
        type: CONDENSED_SUMMARY_TYPE,
        id: `summary:${owner.summaryId}:${owner.chunkIdx}`,
        raw: { text: owner.summary },
      });
    }
  }
  return out;
}

function blockKind(block: ConversationBlock): string {
  if (typeof block.kind === 'string' && block.kind !== '') {
    return block.kind;
  }
  const phase = str(block.raw, 'phase').trim();
  if (block.type === 'assistant' && phase) {
    return `assistant_${phase}`;
  }
  return block.type;
}

function isAssistantBlock(block: ConversationBlock): boolean {
  return block.type === 'assistant' || blockKind(block).startsWith('assistant_');
}

function isFinalAssistantBlock(block: ConversationBlock): boolean {
  return blockKind(block) === 'assistant_final';
}

/**
 * Return ids for the trailing run of adjacent assistant blocks when that run contains final-answer
 * content. Condensed summaries must never replace only part of that run: parsers may split one final
 * agent message across multiple adjacent assistant rows at blank-line gaps, and the TUI's visual
 * coalescing treats that run as one message.
 */
function trailingFinalAssistantRunIds(blocks: readonly ConversationBlock[]): ReadonlySet<string> {
  const run: ConversationBlock[] = [];
  for (let i = blocks.length - 1; i >= 0; i--) {
    const block = blocks[i];
    if (block === undefined || !isAssistantBlock(block)) {
      break;
    }
    run.push(block);
  }
  if (!run.some(isFinalAssistantBlock)) {
    return new Set<string>();
  }
  const ids = new Set<string>();
  for (const block of run) {
    if (block.id !== null && block.id !== undefined) {
      ids.add(block.id);
    }
  }
  return ids;
}

// ---------------------------------------------------------------------------
// Pure transforms
// ---------------------------------------------------------------------------

/**
 * Convert a raw transcript array for one agent into ordered `ChatTurn[]`.
 * Pure — same input → same output. No React, no store, no bus.
 */
export function selectConversationTurns(
  blocks: readonly ConversationBlock[] | undefined,
): readonly ChatTurn[] {
  if (!blocks || blocks.length === 0) return [];
  const turns: ChatTurn[] = [];
  const lastBlock = blocks[blocks.length - 1];
  // Trailing-segment heuristic (Textual `_live_choice_prompt`): the final block is a still-open
  // live prompt iff it is an unanswered `choice_prompt`. Computed here, where block position is
  // known — `formatBlock` is position-blind. The text is identical live-vs-finalized; only the
  // `isLivePrompt` flag changes.
  const lastIsLivePrompt =
    lastBlock !== undefined &&
    lastBlock.type === 'choice_prompt' &&
    field(lastBlock.raw, 'answered') !== true;
  for (let i = 0; i < blocks.length; i++) {
    const block = blocks[i];
    if (block === undefined) continue;
    const turn = formatBlock(block);
    if (turn === null) continue;
    if (i === blocks.length - 1 && lastIsLivePrompt) {
      turns.push({ ...turn, isLivePrompt: true });
    } else {
      turns.push(turn);
    }
  }
  return turns;
}

/**
 * Build the full view-model for one agent's conversation.
 *
 * `/clear` floor (user ask #5): blocks at or below `clearedFloors[agentId]` are filtered out before
 * formatting, so a local `/clear` wipes the view even though the authoritative snapshot re-pulls the
 * old (durably-logged) blocks on reconnect. Blocks with no numeric id are kept (they predate ids /
 * can't be compared — never hide content we can't position). Absent floor = show everything.
 */
export function selectConversationView(
  agentId: string,
  state: ConversationsState,
  viewMode: ChatViewMode = 'verbose',
): ConversationView {
  const floor = state.clearedFloors[agentId];
  const floored =
    floor === undefined
      ? state.transcripts[agentId]
      : aboveFloor(state.transcripts[agentId], floor);
  // Condensed view (TUIchat-4): replace each chunk summary's attributed run of blocks with the summary
  // text BEFORE formatting, so the Phase-2 renderer sees a shorter synthetic stream. `assistant_final`
  // is never attributed → always verbatim. Verbose/tmux paths leave `floored` untouched (byte-identical
  // to before this change). `tmux` never reaches this selector (the Stage shows the live frame), but if
  // it did it would render verbose, which is the correct fallback.
  const blocks =
    viewMode === 'condensed' ? condenseBlocks(floored, state.chunkSummaries[agentId]) : floored;
  const turns = selectConversationTurns(blocks);
  return { agentId, turns, hasContent: turns.length > 0 };
}

/** Keep only blocks whose numeric id is strictly above `floor`; blocks with a non-numeric/absent id
 * are kept (uncomparable → never hidden). Returns the input untouched when there is nothing to drop. */
function aboveFloor(
  blocks: readonly ConversationBlock[] | undefined,
  floor: number,
): readonly ConversationBlock[] | undefined {
  if (blocks === undefined || blocks.length === 0) {
    return blocks;
  }
  return blocks.filter((block) => {
    const n = block.id === null || block.id === undefined ? Number.NaN : Number(block.id);
    return !Number.isFinite(n) || n > floor;
  });
}

/**
 * Collect every user-typed message across ALL agents' transcripts (chat-input overhaul, user ask #4),
 * sorted by numeric block id ascending (oldest→newest) — the seed for the murder-wide send-history
 * recall ring ({@link ../input/chatHistoryStore.js}). Reads `type==='user'` blocks' `raw.text`; skips
 * empty / non-numeric-id blocks (the latter have no stable sort position). Pure.
 */
export function selectUserHistory(state: ConversationsState): readonly string[] {
  const collected: { id: number; text: string }[] = [];
  for (const blocks of Object.values(state.transcripts)) {
    for (const block of blocks) {
      if (block.type !== 'user') {
        continue;
      }
      const n = block.id === null || block.id === undefined ? Number.NaN : Number(block.id);
      if (!Number.isFinite(n)) {
        continue;
      }
      const text = str(block.raw, 'text').trim();
      if (text === '') {
        continue;
      }
      collected.push({ id: n, text });
    }
  }
  collected.sort((a, b) => a.id - b.id);
  return collected.map((c) => c.text);
}

/**
 * Derive the ordered list of favorited crow chat panes to render.
 * Ordering: collaborator → planners → rogue crows → ticket crows (spec order, same as CrowsPanel).
 *
 * Filtered to: identities favorited per {@link ../selectors/favoritesSelectors.js isFavorited} —
 * which ORs the kind-derived default ({@link ./agentIdentity.js isDefaultFavorited}: collaborator +
 * rogues) with the explicit, persisted favorite set (C11). So a planner or ticket crow the user
 * stars with `ctrl+s` now gets a chat pane too, not only the default-favorited kinds.
 *
 * `favorites` is optional: when omitted (C10-era callers), it falls back to defaults-only — the same
 * behaviour as before C11, so nothing breaks if a caller hasn't been updated.
 */
/** Spec-defined group order for favorites (collaborator → planner → rogue → ticket). */
const FAVORITES_GROUP_ORDER = ['collaborator', 'planner', 'rogue', 'ticket'] as const;

/** An empty favorite set — the defaults-only fallback when no prefs slice is supplied. */
const NO_FAVORITES: FavoritesState = { ids: new Set<string>(), status: 'idle', error: null };

export function selectFavoritesChatPanes(
  rosterState: RosterState,
  favorites: FavoritesState = NO_FAVORITES,
): FavoritesChatPanesView {
  const panes: AgentIdentity[] = [];
  // Collect by group so we maintain the spec order (collaborator → planner → rogue → ticket).
  const byGroup: Record<string, AgentIdentity[]> = {
    collaborator: [],
    planner: [],
    rogue: [],
    ticket: [],
  };

  for (const row of rosterState.rows) {
    const identity = deriveAgentIdentity(row);
    if (
      identity !== null &&
      isFavorited(favorites, identity.agentId, isDefaultFavorited(identity))
    ) {
      const groupKey = identity.kind === 'planner' ? 'planner' : identity.kind;
      (byGroup[groupKey] ?? []).push(identity);
    }
  }

  // Emit in spec order.
  for (const kind of FAVORITES_GROUP_ORDER) {
    const group = byGroup[kind];
    if (group) {
      for (const identity of group) {
        panes.push(identity);
      }
    }
  }

  return { panes };
}

/**
 * Whether one agent's chat pane is OPEN — the favorites default merged with the explicit override
 * (item 9b). An explicit `paneOverrides` entry wins (the user said open/close); absent, it falls
 * through to the favorites default (`isFavorited` ORs the explicit star set with the kind-default,
 * so collaborator + rogues are open by default). The single home for the "is this pane open?"
 * question, used by `selectOpenChatPanes` and `CrowsPanel`'s toggle.
 */
export function isChatPaneOpen(
  identity: AgentIdentity,
  favorites: FavoritesState,
  overrides: ReadonlyMap<string, boolean>,
): boolean {
  const override = overrides.get(identity.agentId);
  if (override !== undefined) {
    return override;
  }
  return isFavorited(favorites, identity.agentId, isDefaultFavorited(identity));
}

/**
 * Derive the ordered list of OPEN chat panes (item 9b) — the favorites default layered under the
 * explicit `paneOverrides`. Replaces {@link selectFavoritesChatPanes} at the Stage call site: an
 * agent's pane is open iff {@link isChatPaneOpen}, so a toggled-open planner appears and a
 * toggled-closed rogue disappears. Ordering is the same spec order (collaborator → planner → rogue
 * → ticket). `favorites`/`overrides` default to empty so a bare caller still renders the kind-default
 * panes.
 */
export function selectOpenChatPanes(
  rosterState: RosterState,
  favorites: FavoritesState = NO_FAVORITES,
  overrides: ReadonlyMap<string, boolean> = NO_OVERRIDES,
): OpenChatPanesView {
  const panes: AgentIdentity[] = [];
  const byGroup: Record<string, AgentIdentity[]> = {
    collaborator: [],
    planner: [],
    rogue: [],
    ticket: [],
  };

  for (const row of rosterState.rows) {
    const identity = deriveAgentIdentity(row);
    if (identity !== null && isChatPaneOpen(identity, favorites, overrides)) {
      const groupKey = identity.kind === 'planner' ? 'planner' : identity.kind;
      (byGroup[groupKey] ?? []).push(identity);
    }
  }

  for (const kind of FAVORITES_GROUP_ORDER) {
    const group = byGroup[kind];
    if (group) {
      for (const identity of group) {
        panes.push(identity);
      }
    }
  }

  return { panes };
}

/** An empty pane-override map — the defaults-only fallback. */
const NO_OVERRIDES: ReadonlyMap<string, boolean> = new Map<string, boolean>();

// ---------------------------------------------------------------------------
// Component-facing hooks (rule 2: memoised on slice identity)
// ---------------------------------------------------------------------------

/**
 * Memoised hook for one agent's conversation turns. Re-runs only when the agent's transcript
 * array ref-changes (which happens only on `applyBlock` for that agent — other agents' arrays
 * keep identity per the ref-swap granularity contract).
 */
export function useConversationTurns(
  agentId: string,
  state: ConversationsState,
  viewMode: ChatViewMode = 'verbose',
): readonly ChatTurn[] {
  const blocks = state.transcripts[agentId];
  const summaries = state.chunkSummaries[agentId];
  const floor = state.clearedFloors[agentId];
  // Memoise on the inputs the view depends on: the transcript ref (changes only on this agent's
  // applyBlock), the chunk-summaries ref (changes only on this agent's chunk-summary update), the
  // agentId, the mode, and the /clear floor. Verbose ignores `summaries` but listing it is harmless
  // (its ref only changes for this agent). Routes through `selectConversationView` so the condensed
  // attribution-replacement logic lives in exactly one place.
  // biome-ignore lint/correctness/useExhaustiveDependencies: selectConversationView reads exactly these inputs off `state`; the listed deps are complete and minimal
  return useMemo(
    () => selectConversationView(agentId, state, viewMode).turns,
    [blocks, summaries, agentId, viewMode, floor],
  );
}

/**
 * Memoised hook for the favorited chat panes list. Re-runs when the roster OR favorites ref-changes
 * (so starring a crow updates the pane list). `favorites` defaults to defaults-only when omitted.
 */
export function useFavoritesChatPanes(
  rosterState: RosterState,
  favorites: FavoritesState = NO_FAVORITES,
): FavoritesChatPanesView {
  return useMemo(() => selectFavoritesChatPanes(rosterState, favorites), [rosterState, favorites]);
}

/**
 * Memoised hook for the OPEN chat panes (item 9b). Re-runs when the roster, favorites, OR the
 * `paneOverrides` map ref-changes (every override mutation ref-swaps the map). The Stage uses this
 * in place of {@link useFavoritesChatPanes} so toggling a pane open/closed re-tiles the center.
 */
export function useOpenChatPanes(
  rosterState: RosterState,
  favorites: FavoritesState = NO_FAVORITES,
  overrides: ReadonlyMap<string, boolean> = NO_OVERRIDES,
): OpenChatPanesView {
  return useMemo(
    () => selectOpenChatPanes(rosterState, favorites, overrides),
    [rosterState, favorites, overrides],
  );
}

/**
 * Derive the `agentId` for the currently active chat pane.
 * Used by the ChatInput (or a future integrated send path) to route `ctrl+enter` to the right agent.
 *
 * Resolution order:
 *  1. `activePaneAgentId` if set (user-pinned).
 *  2. First default-favorited crow in spec order (collaborator → rogue).
 *  3. `null` if no agents are in the roster (nothing to send to).
 *
 * Rule 2: derivation here, not in a component.
 */
export function selectActiveAgentId(
  conversationsState: ConversationsState,
  rosterState: RosterState,
  favorites: FavoritesState = NO_FAVORITES,
): string | null {
  if (conversationsState.activePaneAgentId !== null) {
    return conversationsState.activePaneAgentId;
  }
  // Default the target to the first OPEN pane (item 9b: open = favorites default + overrides) so the
  // chat input names a target whose pane is actually on the Stage.
  const { panes } = selectOpenChatPanes(rosterState, favorites, conversationsState.paneOverrides);
  return panes.length > 0 ? (panes[0]?.agentId ?? null) : null;
}

/** The result of cycling the chat target (item 9 super-chords): the agent now targeted, and whether
 * its pane needs opening (it was a favorited crow whose pane is currently closed). `null` when there
 * is nothing to cycle to. */
export interface CycleTargetResult {
  readonly agentId: string;
  /** True when the landed-on target's pane is currently closed, so the caller must open it. */
  readonly needsOpen: boolean;
}

/**
 * The ordered list of chat-target identities to cycle through (item 9 super-chords): EVERY chattable
 * crow in the roster, in spec group order (collaborator → planner → rogue → ticket). Cycling the
 * target is a pure input-routing change — it does NOT open the crow's pane on the Stage (the user
 * toggles a pane explicitly with the `toggleTargetPane` chord), so the cycle reaches every crow you
 * can chat to, whether or not its chat box is pinned to the Stage. Pure over the roster alone
 * (favorites/overrides no longer gate it — they only decide which panes are *shown*).
 *
 * `conversationsState` / `favorites` are kept in the signature so existing call sites are unchanged
 * and a future ordering tweak can read them without a churny re-thread.
 */
export function selectCycleTargets(
  _conversationsState: ConversationsState,
  rosterState: RosterState,
  _favorites: FavoritesState = NO_FAVORITES,
): readonly AgentIdentity[] {
  const byGroup: Record<string, AgentIdentity[]> = {
    collaborator: [],
    planner: [],
    rogue: [],
    ticket: [],
  };
  for (const row of rosterState.rows) {
    const identity = deriveAgentIdentity(row);
    if (identity !== null) {
      const groupKey = identity.kind === 'planner' ? 'planner' : identity.kind;
      (byGroup[groupKey] ?? []).push(identity);
    }
  }
  const targets: AgentIdentity[] = [];
  for (const kind of FAVORITES_GROUP_ORDER) {
    for (const identity of byGroup[kind] ?? []) {
      targets.push(identity);
    }
  }
  return targets;
}

/**
 * The chat-target identities immediately before/after the current target in {@link selectCycleTargets}
 * — what `cycleTargetPrev` (`◂`) and `cycleTargetNext` (`▸`) would land on. Used by the
 * {@link ../components/ChatInput.js ChatInput} to advertise the adjacent crows on its bottom border
 * so the user can see who a step in each direction reaches WITHOUT opening any pane.
 *
 * Both are `null` when there are fewer than two targets (nothing to cycle to). With exactly two,
 * prev and next are the same other crow (a single step wraps either way) — both are returned so each
 * side of the border still names it.
 */
export function selectAdjacentTargets(
  conversationsState: ConversationsState,
  rosterState: RosterState,
  favorites: FavoritesState = NO_FAVORITES,
): { readonly prev: AgentIdentity | null; readonly next: AgentIdentity | null } {
  const targets = selectCycleTargets(conversationsState, rosterState, favorites);
  if (targets.length < 2) {
    return { prev: null, next: null };
  }
  const current = selectActiveAgentId(conversationsState, rosterState, favorites);
  const idx = targets.findIndex((t) => t.agentId === current);
  const len = targets.length;
  // Unknown current → step from before-the-start so prev/next still name the list's ends.
  const from = idx === -1 ? 0 : idx;
  const prev = targets[(((from - 1) % len) + len) % len] ?? null;
  const next = targets[(from + 1) % len] ?? null;
  return { prev, next };
}

/**
 * Compute the chat target after stepping `direction` (+1 next, −1 prev) from the current active
 * target through {@link selectCycleTargets}. Wraps around the list. When the current target is not in
 * the list (or there is no current target), starts from the first/last entry so the chord still has an
 * effect. Returns `null` when there is nothing to cycle to (no open panes and no favorites).
 *
 * The returned `needsOpen` flag reports whether the landed-on target's pane is currently closed.
 * It is informational only: cycling NO LONGER opens the pane (the user toggles a pane explicitly via
 * `toggleTargetPane`), so a step can target a crow whose chat box is not on the Stage.
 */
export function selectCycledTarget(
  conversationsState: ConversationsState,
  rosterState: RosterState,
  favorites: FavoritesState,
  direction: 1 | -1,
): CycleTargetResult | null {
  const targets = selectCycleTargets(conversationsState, rosterState, favorites);
  if (targets.length === 0) {
    return null;
  }
  const current = selectActiveAgentId(conversationsState, rosterState, favorites);
  const currentIndex = targets.findIndex((t) => t.agentId === current);
  // Not found → step from before the start (next) / after the end (prev) so the first chord lands on
  // the first/last entry.
  const from = currentIndex === -1 ? (direction === 1 ? -1 : targets.length) : currentIndex;
  const len = targets.length;
  const nextIndex = (((from + direction) % len) + len) % len;
  const landed = targets[nextIndex];
  if (landed === undefined) {
    return null;
  }
  const needsOpen = !isChatPaneOpen(landed, favorites, conversationsState.paneOverrides);
  return { agentId: landed.agentId, needsOpen };
}

/**
 * The currently-targeted agent's identity (label + kind), for the chat input to display *who a typed
 * message will go to*. Resolves the active `agentId` via {@link selectActiveAgentId}, then maps it
 * back to its roster row → {@link AgentIdentity} (carrying the human label). Returns `null` when there
 * is no target (empty roster) — the chat input then shows its neutral placeholder.
 *
 * Falls back to a synthetic collaborator-shaped identity (label = the raw id) if the active id is set
 * but no longer in the roster, so a pinned-but-departed target still names itself rather than vanish.
 */
export function selectActiveAgent(
  conversationsState: ConversationsState,
  rosterState: RosterState,
  favorites: FavoritesState = NO_FAVORITES,
): AgentIdentity | null {
  const agentId = selectActiveAgentId(conversationsState, rosterState, favorites);
  if (agentId === null) {
    return null;
  }
  for (const row of rosterState.rows) {
    if (row.agentId === agentId) {
      const identity = deriveAgentIdentity(row);
      if (identity !== null) {
        return identity;
      }
    }
  }
  return { kind: 'collaborator', agentId, label: agentId };
}

// ---------------------------------------------------------------------------
// Live choice prompt (chat-input takeover) + conversation meta
// ---------------------------------------------------------------------------

/** One option of a live multiple-choice dialog, display-ready. `checked` is null on single-select
 * menus and the checkbox state on multi-select (CC AskUserQuestion multiSelect) menus. */
export interface ChoicePromptOptionView {
  readonly number: number;
  readonly label: string;
  readonly description: string | null;
  readonly checked: boolean | null;
}

/** The live (unanswered, trailing) choice prompt for an agent — the view the chat input's
 * multiple-choice takeover renders. `selected` is the option NUMBER under the dialog cursor
 * (parser ground truth, updated via block-updated events as the cursor moves in the pane), or
 * null when the cursor sits on the multi-select's dedicated unnumbered Submit row. */
export interface LiveChoicePromptView {
  readonly question: string;
  readonly options: readonly ChoicePromptOptionView[];
  readonly selected: number | null;
  readonly multi: boolean;
  readonly footer: string | null;
}

/**
 * The still-open multiple-choice dialog for `agentId`, or null. Same trailing-segment heuristic as
 * `ChatTurn.isLivePrompt` (a live wizard is always the trailing block, unanswered) — this selector
 * surfaces it as a typed view for the chat-input takeover instead of a text turn flag.
 */
export function selectLiveChoicePrompt(
  state: ConversationsState,
  agentId: string | null,
): LiveChoicePromptView | null {
  if (agentId === null) return null;
  const blocks = state.transcripts[agentId];
  if (!blocks || blocks.length === 0) return null;
  const last = blocks[blocks.length - 1];
  if (last === undefined || last.type !== 'choice_prompt') return null;
  const raw = last.raw;
  if (field(raw, 'answered') === true) return null;
  const question = str(raw, 'question').trim();
  const rawOptions = field(raw, 'options');
  if (!question || !Array.isArray(rawOptions)) return null;
  const options: ChoicePromptOptionView[] = [];
  for (const option of rawOptions) {
    if (option === null || typeof option !== 'object' || Array.isArray(option)) continue;
    const optRec = option as Readonly<Record<string, unknown>>;
    const number = field(optRec, 'number');
    const label = str(optRec, 'label').trim();
    if (typeof number !== 'number' || !label) continue;
    const desc = str(optRec, 'description').trim();
    const checked = field(optRec, 'checked');
    options.push({
      number,
      label,
      description: desc || null,
      checked: typeof checked === 'boolean' ? checked : null,
    });
  }
  if (options.length === 0) return null;
  const selected = field(raw, 'selected');
  const footer = str(raw, 'footer').trim();
  const multi = field(raw, 'multi') === true;
  return {
    question,
    options,
    // `selected: null` is the parser saying the cursor is on the multi-select Submit row; on a
    // single-select it can only mean a malformed payload, so fall back to the first option.
    selected: typeof selected === 'number' ? selected : multi ? null : (options[0]?.number ?? 1),
    multi,
    footer: footer || null,
  };
}

/** CC's freeform "Type something." option — selecting it puts the dialog into (or right before)
 * free-text entry. Matched leniently because CC renders it "Type something." on single-select menus
 * and "Type something" on multi-select ones. */
export function isFreeformChoiceLabel(label: string): boolean {
  return /^type something\.?$/i.test(label.trim());
}

/** True when the dialog cursor sits on the single-select freeform option. Only single-select menus
 * get the local-compose takeover (multi-select drives checkboxes + a Submit row, a different flow),
 * so this gates that path. Drives both key routing (App.tsx) and rendering (ChatInput.tsx) off one
 * predicate, so what is shown and how keys are handled never disagree. */
export function isFreeformChoiceSelected(prompt: LiveChoicePromptView): boolean {
  if (prompt.multi || prompt.selected === null) return false;
  const option = prompt.options.find((o) => o.number === prompt.selected);
  return option !== undefined && isFreeformChoiceLabel(option.label);
}

/** Null-safe meta lookup: the agent's liveness pair, defaulting to nulls when unknown. */
const EMPTY_META: ConversationMeta = { liveState: null, queuedMessage: null };

export function selectConversationMeta(
  state: ConversationsState,
  agentId: string | null,
): ConversationMeta {
  if (agentId === null) return EMPTY_META;
  return state.meta[agentId] ?? EMPTY_META;
}

/** Memoised hook for the active chat target's identity — re-runs when conversations/roster/favorites
 * ref-change. Used by the {@link ../components/ChatInput.js ChatInput} to label its target. */
export function useActiveAgent(
  conversationsState: ConversationsState,
  rosterState: RosterState,
  favorites: FavoritesState = NO_FAVORITES,
): AgentIdentity | null {
  return useMemo(
    () => selectActiveAgent(conversationsState, rosterState, favorites),
    [conversationsState, rosterState, favorites],
  );
}
