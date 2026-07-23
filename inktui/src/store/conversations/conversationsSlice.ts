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
 *
 * IMPORTANT — the real wire shape (Python `block_to_wire`, see
 * `murder/state/persistence/conversation.py`) is the conversation_blocks ROW, not the segment:
 *   `{ id: int, conversation_id, ordinal, kind, payload, sealed, service_received_at }`
 * The original parsed segment dict (`{type:'user'|'assistant'|'tool_call'|…, text, title, …}`)
 * lives *nested* under `payload`. `kind` is the storage discriminant (e.g. `assistant_intermediate`
 * vs `assistant_final`); the segment's own `payload.type` is the display discriminant the
 * selectors switch on. `id` is a NUMERIC row id.
 *
 * This DTO normalises that wire row into a selector-friendly shape:
 *  - `type` is `payload.type` (the segment discriminant the selectors render on).
 *  - `id` is `String(wire.id)` — the row id, stringified so `block-updated` replace-by-id matches.
 *  - `raw` is the `payload` (the segment dict) — so selectors read content fields directly off it.
 *
 * Anchored by the cross-language golden contract test
 * (`inktui/test/store/conversations/conversationBlockContract.test.ts` +
 * `tests/unit/test_conversation_block_golden.py`): if either side's keys/types drift, a test fails.
 */
export interface ConversationBlock {
  /** Segment discriminant (`payload.type`: 'user', 'assistant', 'tool_call', 'plan_update', …). */
  readonly type: string;
  /** Storage-row discriminant (`kind`: 'assistant_intermediate', 'assistant_final', …), when known. */
  readonly kind?: string | null;
  /** Row id (stringified) — used by `block-updated` to replace a trailing block in place. */
  readonly id?: string | null;
  /** The segment dict (`payload`) — selectors read content fields (text/title/options/…) off it. */
  readonly raw: Record<string, unknown>;
}

/**
 * Parse a wire `ConversationBlockEvent.block` row into our typed DTO. Pure.
 *
 * Unwraps the storage row: reads the numeric `id`, and pulls the segment dict out of `payload`.
 * The segment's `type` (not the row's `kind`) is the selector discriminant. Defensive: if `payload`
 * is absent (a future flat shape or a malformed event), falls back to treating the row itself as the
 * segment so nothing crashes — but the golden contract test pins the real nested shape.
 */
export function parseBlock(raw: Record<string, unknown>): ConversationBlock {
  // biome-ignore lint/complexity/useLiteralKeys: noPropertyAccessFromIndexSignature (tsconfig strict) requires bracket notation on index-signature types; these are runtime field reads on an opaque wire record.
  const idVal = raw['id'];
  // biome-ignore lint/complexity/useLiteralKeys: same — opaque wire record requires bracket access
  const kindVal = raw['kind'];
  // biome-ignore lint/complexity/useLiteralKeys: same — opaque wire record requires bracket access
  const payloadVal = raw['payload'];
  const payload =
    payloadVal !== null && typeof payloadVal === 'object' && !Array.isArray(payloadVal)
      ? (payloadVal as Record<string, unknown>)
      : raw;
  // biome-ignore lint/complexity/useLiteralKeys: same — opaque segment record requires bracket access
  const typeVal = payload['type'];
  // `id` is a numeric row id on the wire; stringify it so block-updated replace-by-id matches.
  // (A string id — e.g. a hand-built test/legacy event — is taken as-is.)
  const id = typeof idVal === 'number' ? String(idVal) : typeof idVal === 'string' ? idVal : null;
  return {
    type: typeof typeVal === 'string' ? typeVal : 'unknown',
    kind: typeof kindVal === 'string' ? kindVal : null,
    id,
    raw: payload,
  };
}

/**
 * The conversations slice state. Keyed by `agentId`; each value is an ordered array of blocks
 * (append/update semantics from `ConversationBlockEvent`). All fields readonly — ref-swapped
 * wholesale on change at the per-agent transcript level (not the whole map).
 *
 * `activePaneAgentId`: the currently visible transcript pane agent. `null` = no explicit
 * selection; the panel defaults to the collaborator or first favorited crow. This is C10's seam
 * for "keep transcript pane active" (the ctrl+s/starring side is C11's job — see seam note at
 * bottom).
 */
/**
 * Per-agent conversation liveness, fed by `ConversationStateEvent` (and primed from the
 * `state.conversations_snapshot` `live_state`/`queued_message` columns). `liveState` is the parsed
 * harness UI state (`working` / `awaiting_input` / `awaiting_approval`); `queuedMessage` is a user
 * message the service accepted while the harness was busy, held for idle delivery — the chat input
 * renders it as the one-line "queued" row and lets Enter interrupt-to-send-now.
 */
export interface ConversationMeta {
  readonly liveState: string | null;
  readonly queuedMessage: string | null;
}

/**
 * One rolling chunk summary for an agent's Condensed view (TUIchat-4). Mirrors the Python
 * `ConversationChunkSummary` DTO (`murder/app/protocol/read_models.py`); `dto_to_wire` preserves the
 * snake_case field names, so the wire shape is `{summary_id, chunk_idx, summary, block_ids}`.
 *
 * Attribution contract (TUIchatpaneupgrade Phase 4, Scope-decisions #3): `summary` stands in for
 * EXACTLY the blocks whose ids are in `blockIds` (explicit pointers into `conversation_blocks.id`,
 * the same numeric row id `ConversationBlock.id` stringifies). The Condensed selector replaces the
 * run of those blocks with a single synthetic summary block. Final (`assistant_final`) blocks are
 * NEVER attributed to a summary, so they always render verbatim.
 *
 * `blockIds` are kept NUMERIC here (as on the wire) — the selector stringifies on comparison, since
 * `ConversationBlock.id` is the stringified row id.
 */
export interface ChunkSummary {
  /** Summary row PK (ordered by `chunkIdx`; absent on a live `chunk-summarized` event → -1). */
  readonly summaryId: number;
  /** Chunk ordinal (ascending = conversation order; -1 on a live event with no ordinal). */
  readonly chunkIdx: number;
  /** The summary text that stands in for the attributed blocks in Condensed view. */
  readonly summary: string;
  /** Numeric ids of the conversation blocks this summary covers (the attribution pointers). */
  readonly blockIds: readonly number[];
}

export interface ConversationsState {
  /** Per-agent block transcript. Only agents with at least one block have an entry. */
  readonly transcripts: Readonly<Record<string, readonly ConversationBlock[]>>;
  /** Per-agent liveness ({@link ConversationMeta}); absent entry = unknown (treated as nulls). */
  readonly meta: Readonly<Record<string, ConversationMeta>>;
  /**
   * The transcript pane currently pinned open by user action (`ctrl+s` "keep pane active" path).
   * `null` = no user-pinned selection; the panel derives the displayed pane from favorites.
   * C11 is responsible for the full starring/prefs system; C10 provides this slot + the
   * "keep active on send" behavior.
   */
  readonly activePaneAgentId: string | null;
  /**
   * Explicit open/close overrides for transcript panes, layered OVER the favorites-derived default
   * (item 9b). A `true` opens a pane that the favorites default would close (e.g. a planner the
   * user hasn't starred); a `false` closes a pane the favorites default would open (e.g. a rogue,
   * which is default-favorited). Absent (no entry) → fall through to the favorites default. The map
   * is the user's explicit "show this pane / hide this pane" intent; `selectOpenTranscriptPanes`
   * merges it with the favorites default to decide which panes the Stage tiles.
   */
  readonly paneOverrides: ReadonlyMap<string, boolean>;
  /**
   * Monotonic activation age for non-required stage transcript panes. The active pane is reset to 0; every
   * existing nonzero age bumps when a different pane becomes active, making older inactive histories
   * more eligible for layout reaping without changing the central reap algorithm.
   */
  readonly paneReapAges: ReadonlyMap<string, number>;
  /**
   * Per-agent `/clear` floor (chat-input overhaul, user ask #5): the max numeric block id present
   * when the user ran `/clear`. The render selector ({@link ../../selectors/conversationsSelectors.js
   * selectConversationView}) hides blocks at or below this floor, so the local view clears even though
   * the authoritative snapshot re-pulls the (durably-logged) old blocks on reconnect. Absent entry =
   * no floor (show everything). The old chat is never lost — it lives server-side.
   */
  readonly clearedFloors: Readonly<Record<string, number>>;
  /**
   * Per-pane chat view mode (TUIchat-3): `verbose` (today's full render) / `condensed` (rolling
   * chunked summaries, backend lands in TUIchat-4) / `tmux` (inline tmux frame, TUIchat-5). Ephemeral
   * and NOT persisted (mirrors `paneOverrides`' per-`agentId` intent map). Absent entry → fall through
   * to `settings.defaultChatViewMode`. Effective mode = `paneViewModes[agentId] ?? defaultChatViewMode`.
   */
  readonly paneViewModes: Readonly<Record<string, ChatViewMode>>;
  /**
   * Per-agent rolling chunk summaries for the Condensed view (TUIchat-4). Ordered by `chunkIdx`
   * ascending. Ephemeral (mirrors the other snapshot-fed fields, e.g. `meta`): primed from the
   * `state.conversations_snapshot` `chunk_summaries[]` (the source of truth) and incrementally folded
   * from live `conversation.block` / `chunk-summarized` events so Condensed updates without waiting
   * for a full re-snapshot. Absent/empty entry → Condensed falls back to verbose-like (intermediates
   * render as-is — never blank). Consumed by `selectConversationView` when a pane's effective mode is
   * `condensed`.
   */
  readonly chunkSummaries: Readonly<Record<string, readonly ChunkSummary[]>>;
}

/** Per-pane chat view mode (TUIchat-3). `tmux` is reachable only via the cycle, not a settable default. */
export type ChatViewMode = 'verbose' | 'condensed' | 'tmux';

/** Initial (empty) state — no transcripts, no active pane. */
export const initialConversationsState: ConversationsState = {
  transcripts: {},
  meta: {},
  activePaneAgentId: null,
  paneOverrides: new Map<string, boolean>(),
  paneReapAges: new Map<string, number>(),
  clearedFloors: {},
  paneViewModes: {},
  chunkSummaries: {},
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
