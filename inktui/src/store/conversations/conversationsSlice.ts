/**
 * Conversations slice — per-agent chat transcript, fed by `ConversationBlockEvent` off the bus.
 *
 * Deliberately NOT a `listSlice.ts` factory shell. Reasons:
 *  - The factory is for `{rows,status,error}` re-pulled on a `state.snapshot` entity event.
 *    `'conversation'` is NOT in the `Entity` union (protocol.ts line 60), so this slice is never
 *    snapshot-invalidated and must handle its own event subscription (see `store.ts`).
 *  - Data arrives as content-bearing appends/updates (`ConversationBlockEvent`), not "re-pull the
 *    whole list". Each event appends/replaces one block in one agent's transcript — a splice, not a
 *    replace.
 *  - The map is keyed by `agent_id` (from `BaseEvent`, always present) — no `conversation_id`
 *    parsing anywhere. The Python `ConversationsStore.conversation_id_for_agent` is 1:1; we treat
 *    it as such and key by agentId directly (CONTRACT ASSUMPTION: one conversation per agent).
 *
 * Shape follows `ticketDetail` as precedent for a hand-written, non-factory, non-snapshot slice.
 *
 * Ref-swap granularity: `applyBlock` produces `{...prev, [agentId]: updatedBlocks}` — other agents'
 * arrays keep identity, so per-pane `memo`'d components only re-render for the agent whose history
 * changed.
 */

import type { StateCreator } from 'zustand';
import type { AppStore } from '../store.js';

/**
 * One conversation block as it arrives over the wire (`ConversationBlockEvent.block`).
 * The wire type is `Record<string,unknown>` (opaque above the transport seam); we layer a minimal
 * typed DTO above it here for selector use. `type` is the block discriminant; `id` is the
 * block's own identity (used by `block-updated` to find and replace the trailing block).
 *
 * Only fields the selector/view uses are typed; others flow through as unknown and are ignored.
 * This is intentional: the block schema is service-side; we don't want to couple tightly.
 */
export interface ConversationBlock {
  /** Wire block discriminant (e.g. 'user', 'assistant', 'tool_call', 'plan_update', …). */
  readonly type: string;
  /** Block's own id — used by `block-updated` to replace a trailing block in place. */
  readonly id?: string | null;
  /** Raw wire record for fields the selector reads but the DTO doesn't name. */
  readonly raw: Record<string, unknown>;
}

/** Parse a wire `Record<string,unknown>` block into our typed DTO. Pure. */
export function parseBlock(raw: Record<string, unknown>): ConversationBlock {
  // biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature (tsconfig strict) requires bracket notation on index-signature types; these are runtime field reads on an opaque wire record.
  const typeVal = raw['type'];
  // biome-ignore lint/complexity/useLiteralKeys: same — opaque wire record requires bracket access
  const idVal = raw['id'];
  return {
    type: typeof typeVal === 'string' ? typeVal : 'unknown',
    id: typeof idVal === 'string' ? idVal : null,
    raw,
  };
}

/**
 * The conversations slice state. Keyed by `agentId`; each value is an ordered array of blocks
 * (append/update semantics from `ConversationBlockEvent`). All fields readonly — ref-swapped
 * wholesale on change at the per-agent transcript level (not the whole map).
 *
 * `activePaneAgentId`: the currently visible chat pane agent. `null` = no explicit selection;
 * the panel defaults to the collaborator or first favorited crow. This is C10's seam for
 * "keep chat pane active" (the ctrl+s/starring side is C11's job — see seam note at bottom).
 */
export interface ConversationsState {
  /** Per-agent block transcript. Only agents with at least one block have an entry. */
  readonly transcripts: Readonly<Record<string, readonly ConversationBlock[]>>;
  /**
   * The chat pane currently pinned open by user action (`ctrl+s` "keep pane active" path).
   * `null` = no user-pinned selection; the panel derives the displayed pane from favorites.
   * C11 is responsible for the full starring/prefs system; C10 provides this slot + the
   * "keep active on send" behavior.
   */
  readonly activePaneAgentId: string | null;
}

/** Initial (empty) state — no transcripts, no active pane. */
export const initialConversationsState: ConversationsState = {
  transcripts: {},
  activePaneAgentId: null,
};

/**
 * Slice factory. Not a `createListSlice` shell — this slice has its own shape.
 * Contributes only the `conversations` key; `../store.ts` composes it.
 * No `*_INVALIDATING_ENTITY` — this slice is driven by `conversation.block` events
 * via a second `bus.subscribe` in `store.ts`, not by `state.snapshot` entity events.
 */
export const createConversationsSlice: StateCreator<
  AppStore,
  [],
  [],
  { conversations: ConversationsState }
> = () => ({
  conversations: initialConversationsState,
});
