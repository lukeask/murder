/**
 * Conversations view-models — display-ready chat history per agent.
 *
 * Rule 2 in action: ALL formatting of raw blocks into display turns lives here, never in the store
 * or in components. The `ConversationsState` stores raw `ConversationBlock`s; this selector
 * produces ordered `ChatTurn[]` arrays ready to paint.
 *
 * Block→turn formatting mirrors the shape of Python `conversations.py`'s `_segment_to_turn`,
 * but only the subset the chat pane actually renders: user, assistant, tool_call, plan_update.
 * We intentionally do NOT port every branch of `_segment_to_turn` — only what the component needs
 * to display. Unknown block types are passed through with a fallback label so new service events
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
  ConversationBlock,
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
export type TurnSpeaker = 'user' | 'assistant' | 'tool' | 'plan' | 'agent' | 'notice' | 'unknown';

/** A single display-ready chat turn. */
export interface ChatTurn {
  /** Which agent/role spoke (for coloring). */
  readonly speaker: TurnSpeaker;
  /** Display text (may be multiline). */
  readonly text: string;
  /** The originating block's id, if any (for keying in React lists). */
  readonly blockId: string | null;
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
  for (const block of blocks) {
    const turn = formatBlock(block);
    if (turn !== null) turns.push(turn);
  }
  return turns;
}

/**
 * Build the full view-model for one agent's conversation.
 */
export function selectConversationView(
  agentId: string,
  state: ConversationsState,
): ConversationView {
  const blocks = state.transcripts[agentId];
  const turns = selectConversationTurns(blocks);
  return { agentId, turns, hasContent: turns.length > 0 };
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
): readonly ChatTurn[] {
  const blocks = state.transcripts[agentId];
  // biome-ignore lint/correctness/useExhaustiveDependencies: blocks is the stable ref; agentId is string primitive; both are correct deps
  return useMemo(() => selectConversationTurns(blocks), [blocks, agentId]);
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
  const { panes } = selectFavoritesChatPanes(rosterState, favorites);
  return panes.length > 0 ? (panes[0]?.agentId ?? null) : null;
}
