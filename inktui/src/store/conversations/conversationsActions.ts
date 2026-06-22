/**
 * Conversations actions — the only code that calls the bus for chat operations (rule 3).
 *
 * Three actions:
 *  1. `refresh()` — explicit boot-prime pull. Calls `state.conversations_snapshot` to hydrate the
 *     transcripts map on connect (cold-start, before any `conversation.block` events arrive). The
 *     reply is a list of `ConversationSummary` entries (in-progress conversations); each entry's
 *     `agent_id` becomes the key and its `blocks` are parsed through `parseBlock` (same wire shape
 *     as the event block, so the seam is consistent). Errors are swallowed into the `conversations`
 *     slice (future: add an `error` field when needed). Called from `primeSlices` in `index.tsx` on
 *     every (re)connect.
 *
 *  2. `send(agentId, message)` — the sole sender of chat messages. `agent.message` is an
 *     orchestrator command kind (not a standalone RPC), so this routes through the live
 *     `command.submit` choke point ({@link ../commandSubmit.js}). Routes to the agent identified by
 *     `agentId`; the discriminated-union identity (deriving the right agentId) lives in the
 *     selectors/CrowChatPanel, NOT here (rule 2). This action receives the resolved agentId from its
 *     caller, never parses a conversation_id (rule 1 / anti-pattern).
 *
 *  3. `applyBlock(event)` — pure setState, no bus call. Called by the second `bus.subscribe` in
 *     `store.ts` on each `conversation.block` event. Handles both `block-appended` (push) and
 *     `block-updated` (replace trailing block with matching id). Ref-swaps only the affected
 *     agent's transcript array — sibling agents keep identity.
 *
 * `agent.message` is dispatched as an orchestrator command kind via `command.submit` (the live
 * write seam) rather than as a direct RPC — see {@link ../commandSubmit.js}. The discriminated-union
 * agent identity is resolved by the caller (rule 2); this action just submits the command.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { ConversationBlockEvent, ConversationStateEvent } from '../../bus/protocol.js';
import { submitCommand } from '../commandSubmit.js';
import type { AppStore } from '../store.js';
import { toastStore } from '../toast/toastStore.js';
import {
  type ChatViewMode,
  type ChunkSummary,
  type ConversationBlock,
  type ConversationMeta,
  parseBlock,
} from './conversationsSlice.js';

/**
 * Declares the conversations read RPC via declaration merging rather than editing the frozen C1 bus
 * files. `state.conversations_snapshot` is the bus-contract name (`domain.verb`, mirrors Python
 * `RuntimeClient.get_conversations_snapshot`). Called on connect to prime the transcripts map so a
 * cold-start service (no `conversation.block` events yet) paints populated chat panes immediately.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /**
     * Fetch all in-progress agent conversations as a snapshot. Re-pulled on connect (boot-prime);
     * individual block updates arrive via `conversation.block` events thereafter. The reply is a
     * list of `ConversationSummary` entries (one per in-progress conversation), each carrying the
     * agent_id and the full block history (same `ConversationBlockSummary` wire shape as the event
     * block, so `parseBlock` applies unchanged).
     */
    'state.conversations_snapshot': {
      params: Record<string, never>;
      result: ConversationsSnapshotReply;
    };
  }
}

/**
 * One block as it appears inside `ConversationSummary.blocks` (the `ConversationBlockSummary` DTO,
 * `murder/app/service/client_api.py`). Same nested shape as `ConversationBlockEvent.block`, so
 * `parseBlock` applies unchanged: `id` is numeric, `payload` is the segment dict with `type`.
 */
export interface ConversationBlockSummaryDto {
  id: number | null;
  conversation_id: string;
  ordinal: number;
  kind: string;
  /** The segment dict — `payload.type` is the selector discriminant ('user', 'assistant', …). */
  payload: Record<string, unknown>;
  sealed: boolean;
  service_received_at: string;
}

/**
 * One rolling chunk summary as it arrives inside `ConversationSummary.chunk_summaries[]` (the
 * `ConversationChunkSummary` DTO, `murder/app/service/client_api.py`). `dto_to_wire` preserves the
 * Python snake_case field names verbatim, so the wire shape is exactly this. `block_ids` are the
 * explicit attribution pointers into `conversation_blocks.id` (numeric); the Condensed selector
 * replaces exactly those blocks with `summary`. Ordered by `chunk_idx` on the wire.
 */
export interface ConversationChunkSummaryDto {
  summary_id: number;
  chunk_idx: number;
  summary: string;
  block_ids: readonly number[];
}

/**
 * One conversation entry in the snapshot list (the `ConversationSummary` DTO,
 * `murder/app/service/client_api.py`). Only `in_progress` conversations are included.
 *
 * TUIchat-4: the old single `condensed: string | null` scalar was DROPPED on the backend (column
 * removed in migration) and replaced by `chunk_summaries[]` — ordered rolling chunk summaries, each
 * with its attributed `block_ids`. Empty when no chunk has been summarized yet (Condensed → verbose).
 */
export interface ConversationSummaryDto {
  conversation_id: string;
  agent_id: string;
  harness: string | null;
  model: string | null;
  harness_session_id: string | null;
  live_state: string | null;
  /** Ordered rolling chunk summaries for the Condensed view (TUIchat-4); may be empty. */
  chunk_summaries: readonly ConversationChunkSummaryDto[];
  /** A user message accepted while the harness was busy, held for idle delivery (or null). */
  queued_message?: string | null;
  status: string;
  blocks: readonly ConversationBlockSummaryDto[];
}

/**
 * The `state.conversations_snapshot` reply. Mirrors the service's `ConversationsSnapshot` DTO
 * (`murder/app/service/client_api.py`). `conversations` is a list of `ConversationSummary` entries
 * (only `in_progress` conversations), each carrying the full block history for that agent.
 * Keying is by `agent_id` (CONTRACT ASSUMPTION: one active conversation per agent — same assumption
 * the slice already makes for `conversation.block` events). `parseBlock` applies to each block row
 * unchanged since `ConversationBlockSummary` has the same `id`/`payload` shape as the event block.
 */
export interface ConversationsSnapshotReply {
  conversations: readonly ConversationSummaryDto[];
  /** ISO-8601 datetime string — when the snapshot was taken. */
  as_of: string;
  invalidation_key: string;
}

/** The conversations actions, bound to one `BusClient` + store handle. */
export interface ConversationsActions {
  /**
   * Boot-prime: pull all agent transcripts from `state.conversations_snapshot` and populate the
   * transcripts map. Called from `primeSlices` in `index.tsx` on every (re)connect so a cold-start
   * service (no `conversation.block` events yet) shows populated chat panes immediately.
   *
   * Errors are swallowed (fire-and-forget from the priming path; transcripts remain empty rather
   * than crashing, and live `conversation.block` events will populate them as they arrive).
   */
  refresh(): Promise<void>;

  /**
   * Send a message to the agent identified by `agentId` via `agent.message`.
   * The sole bus caller for chat sends — rule 3. The caller (chat pane / CrowChatPanel)
   * resolves the agentId from the discriminated-union identity BEFORE calling this action.
   * No conversation_id parsing, no string-prefix matching — ever.
   *
   * On success: sets `activePaneAgentId` to `agentId` ("keep pane active" after send).
   * On failure: the action swallows the rejection (logs — callers treat send as fire-and-forget
   * from the UI perspective). The bus-level error policy (timeouts) is the implementation's.
   */
  send(agentId: string, message: string): Promise<void>;

  /**
   * Apply a `ConversationBlockEvent` to the transcript map. Pure `setState` — no bus call.
   * Called by the `store.ts` subscription on each `conversation.block` event.
   *
   * Semantics (mirroring Python `ConversationsStore.apply_event`):
   *  - `block-appended`: push the new block onto the agent's transcript array.
   *  - `block-updated`: replace the last block whose `id` matches — or push if none match
   *    (defensive: `FakeBusClient` tests drive both branches).
   *
   * Ref-swap: produces `{...prev, [agentId]: newArray}` so only the affected agent's transcript
   * changes identity — other agents' arrays are untouched (the granularity contract).
   */
  applyBlock(event: ConversationBlockEvent): void;

  /**
   * Apply a `ConversationStateEvent` to the per-agent meta map. Pure `setState` — no bus call.
   * Called by the `store.ts` subscription on each `conversation.state` event. Ref-swaps only the
   * affected agent's meta entry (granularity contract, same as `applyBlock`).
   */
  applyState(event: ConversationStateEvent): void;

  /**
   * Forward one raw key to the agent's harness pane via the `agent.send_key` orchestrator command.
   * The chat input's multiple-choice takeover uses this to drive a live CC choice dialog (arrows /
   * space / digits / Enter / Esc) — the dialog's ground truth stays in the pane; the parser's
   * `choice_prompt` block updates reflect the move on the next projection tick. `literal=true`
   * sends the key as literal text (printable chars for the dialog's inline "type something" field);
   * `literal=false` sends a tmux key name (`Up`, `Down`, `Enter`, `Escape`, `Space`, `BSpace`).
   * Fire-and-forget from the UI perspective (errors are swallowed like `send`).
   *
   * `enter` (default `false`) appends a real Return after the key — the `/clear` fix (user ask #5):
   * `literal=true, enter=true` types the text then submits it (the bug was sending `/clear\n` as
   * literal text, where the `\n` never submitted). Existing callers omit it (stay `enter:false`).
   */
  sendKey(agentId: string, key: string, literal: boolean, enter?: boolean): Promise<void>;

  /**
   * Clear the local chat view for `agentId` (user ask #5): set the per-agent cleared floor to the
   * current max numeric block id, so {@link ../../selectors/conversationsSelectors.js
   * selectConversationView} hides every block at or below it. The authoritative snapshot still
   * re-pulls the old (durably-logged) blocks on reconnect, but they stay below the floor. No bus call.
   */
  clearTranscript(agentId: string): void;

  /**
   * Interrupt the agent's harness (the `agent.interrupt` orchestrator command). Used by the chat
   * input when a queued message is pending and the user presses Enter: the interrupt stops the
   * current turn, the pane goes input-ready, and the service delivers the queued message on the
   * next projection tick ("send now"). Fire-and-forget; surfaces a toast on submit.
   */
  interrupt(agentId: string): Promise<void>;

  /**
   * Explicitly set the active chat pane. Called by the CrowChatPanel when the user navigates
   * between panes or the "keep pane active" path fires. Does not call the bus.
   * C11 seam: this slot is here for ctrl+s "keep pane active"; the full starring/prefs system
   * (tui.save_favorites) is C11's responsibility.
   */
  setActivePaneAgentId(agentId: string | null): void;

  /**
   * Explicitly open or close a chat pane (item 9b). Writes a `paneOverrides` entry that layers over
   * the favorites-derived default — so `open=true` forces a non-favorited agent's pane visible, and
   * `open=false` hides a default-favorited one. No bus call. Used by `spawnRogue`'s auto-open (9e).
   */
  setChatPaneOpen(agentId: string, open: boolean): void;

  /**
   * Toggle a chat pane open/closed (item 9c). `currentlyOpen` is the pane's CURRENT effective open
   * state (the caller computes it via `selectOpenChatPanes`, which merges the favorites default with
   * the existing override); the action records the override that flips it. No bus call.
   */
  toggleChatPane(agentId: string, currentlyOpen: boolean): void;

  /**
   * Set the chat view mode for a pane (TUIchat-3). Records `paneViewModes[agentId]`, overriding the
   * `settings.defaultChatViewMode`. Ephemeral (not persisted). Used by `:verbose`/`:compact`/`:tmux`.
   * No bus call.
   */
  setPaneViewMode(agentId: string, mode: ChatViewMode): void;

  /**
   * Cycle a pane's chat view mode (TUIchat-3): verbose → condensed → tmux → verbose. Reads the pane's
   * effective mode (`paneViewModes[agentId] ?? settings.defaultChatViewMode`) and writes the next.
   * The `t` (alt+t / ctrl+t) chord's handler. No bus call.
   */
  cyclePaneViewMode(agentId: string): void;
}

export function createConversationsActions(
  bus: BusClient,
  store: StoreApi<AppStore>,
): ConversationsActions {
  // Per-call request token — guards against a stale reply replacing the authoritative set when a
  // reconnect re-prime overlaps two refreshes (same pattern as listSlice.ts / transitActions).
  let seq = 0;
  return {
    async refresh(): Promise<void> {
      const token = ++seq;
      try {
        const reply = await bus.rpc('state.conversations_snapshot', {});
        if (token !== seq) return;
        // Project each ConversationSummary into the transcripts map keyed by agent_id.
        // CONTRACT ASSUMPTION: one active conversation per agent (same assumption the slice makes
        // for `conversation.block` events). `parseBlock` applies to each ConversationBlockSummary
        // row unchanged — the `id`/`payload` shape is identical to the event block shape.
        const parsed: Record<string, readonly ConversationBlock[]> = {};
        const meta: Record<string, ConversationMeta> = {};
        const chunkSummaries: Record<string, readonly ChunkSummary[]> = {};
        for (const conv of reply.conversations) {
          parsed[conv.agent_id] = conv.blocks.map((b) =>
            parseBlock(b as unknown as Record<string, unknown>),
          );
          meta[conv.agent_id] = {
            liveState: conv.live_state ?? null,
            queuedMessage: conv.queued_message ?? null,
          };
          // Chunk summaries (TUIchat-4) — the SOURCE OF TRUTH for the Condensed view. Defensive
          // against an older service with no `chunk_summaries` field (treated as none → Condensed
          // falls back to verbose-like). Normalised to numeric ids + ordered by chunk_idx so the
          // selector can rely on order without re-sorting.
          const rawSummaries = conv.chunk_summaries ?? [];
          chunkSummaries[conv.agent_id] = rawSummaries
            .map(
              (s): ChunkSummary => ({
                summaryId: Number(s.summary_id),
                chunkIdx: Number(s.chunk_idx),
                summary: String(s.summary ?? ''),
                blockIds: (s.block_ids ?? []).map((id) => Number(id)),
              }),
            )
            .sort((a, b) => a.chunkIdx - b.chunkIdx);
        }
        // REPLACE, do not union: the snapshot is authoritative for the in-progress set. A merge
        // (`{...old, ...parsed}`) would keep an agent whose conversation has since ENDED (absent
        // from the snapshot) forever — accumulating ghost panes/dead transcripts across reconnects.
        // The map is rebuilt from exactly the snapshot's conversations.
        store.setState((state) => ({
          conversations: {
            ...state.conversations,
            transcripts: parsed,
            meta,
            // REPLACE (same authoritative-snapshot reasoning as transcripts/meta): the snapshot's
            // chunk_summaries[] is the source of truth, so the map is rebuilt from exactly it. Live
            // `chunk-summarized` events fold in between snapshots via `applyBlock`.
            chunkSummaries,
          },
        }));
      } catch {
        // Swallow: priming is best-effort; live events will hydrate the transcripts when they arrive.
      }
    },

    async send(agentId: string, message: string): Promise<void> {
      try {
        // `agent.message` is an orchestrator command kind, not a standalone RPC — route it through
        // the live `command.submit` choke point (F2). The orchestrator worker dispatches on the kind.
        const result = await submitCommand(bus, 'agent.message', { agent_id: agentId, message });
        // F9 (TODO-T): the send toast is *truth* — pushed here, on the bus ack, not at the keypress
        // (the keypress already cleared the input optimistically; this confirms the round-trip). The
        // branches mirror Textual's `_send_chat` (app.py:1370-1392) faithfully:
        //  - `handled === false` → the agent rejected the message; surface the error and stop (no `→`).
        //  - `queued` (crow busy) → "message queued (crow busy)".
        //  - otherwise → "→ {label}", with the agentId as the label (Textual's own fallback when no
        //    friendly label is threaded; this action only carries agentId — rule 2 keeps labels out).
        // The `→ collaborator` path is NOT reachable here: collaborator chat goes through a different
        // command kind absent from this action, so we don't invent it.
        if (result['handled'] === false) {
          const errorText = String(result['error'] ?? 'agent did not handle message');
          toastStore.getState().push(errorText, { severity: 'error', ttlMs: 12000 });
          return;
        }
        if (result['queued'] === true) {
          toastStore.getState().push('message queued (crow busy)', { ttlMs: 6000 });
        } else {
          toastStore.getState().push(`→ ${agentId}`, { ttlMs: 4000 });
        }
        // Keep the pane for this agent active after sending.
        store.setState((state) => ({
          conversations: { ...state.conversations, activePaneAgentId: agentId },
        }));
      } catch (error: unknown) {
        // Surface, do NOT silently swallow: a dropped/timed-out send used to vanish with no signal,
        // so the user saw "nothing happened" while a message may or may not have gone through. The
        // round-trip failed from the client's view — say so. (The poll loop already resumes through
        // a transient blip; reaching here means the client gave up or the command genuinely failed.)
        const message = error instanceof Error ? error.message : String(error);
        toastStore.getState().push(`send failed: ${message}`, { severity: 'error', ttlMs: 12000 });
      }
    },

    applyBlock(event: ConversationBlockEvent): void {
      const agentId = event.agent_id;

      // TUIchat-4: the Condensed-view chunk summary reuses the `conversation.block` channel with
      // `action: 'chunk-summarized'`; its `block` is the summary payload, NOT a transcript row, so it
      // must never reach `parseBlock`/the transcript array. Fold it into the ephemeral chunkSummaries
      // map as an incremental hint (the snapshot's chunk_summaries[] stays the source of truth and
      // overwrites these on the next re-prime). `summary_id`/`chunk_idx` are absent on the live event:
      // use -1 and append at the tail (events arrive in flush order, the contract).
      if (event.action === 'chunk-summarized') {
        const block = event.block;
        const summaryText = typeof block['summary'] === 'string' ? block['summary'] : '';
        if (!summaryText) return; // empty-summary guard mirrors the backend (Condensed → verbose)
        const rawIds = block['block_ids'];
        const blockIds = Array.isArray(rawIds)
          ? rawIds.map((id) => Number(id)).filter((n) => Number.isFinite(n))
          : [];
        store.setState((state) => {
          const prev = state.conversations.chunkSummaries[agentId] ?? [];
          const next: ChunkSummary = {
            summaryId: -1,
            chunkIdx: prev.length,
            summary: summaryText,
            blockIds,
          };
          return {
            conversations: {
              ...state.conversations,
              chunkSummaries: {
                ...state.conversations.chunkSummaries,
                [agentId]: [...prev, next],
              },
            },
          };
        });
        return;
      }

      const parsed = parseBlock(event.block);

      store.setState((state) => {
        const prev = state.conversations;
        const existing: readonly (typeof parsed)[] = prev.transcripts[agentId] ?? [];

        let updated: readonly (typeof parsed)[];
        if (event.action === 'block-updated') {
          // Replace the last block whose id matches — or push if none match.
          const matchIdx = (() => {
            for (let i = existing.length - 1; i >= 0; i--) {
              const b = existing[i];
              if (b != null && b.id === parsed.id && parsed.id !== null) {
                return i;
              }
            }
            return -1;
          })();
          if (matchIdx !== -1) {
            const arr = [...existing];
            arr[matchIdx] = parsed;
            updated = arr;
          } else {
            updated = [...existing, parsed];
          }
        } else {
          // block-appended: push
          updated = [...existing, parsed];
        }

        return {
          conversations: {
            ...prev,
            transcripts: { ...prev.transcripts, [agentId]: updated },
          },
        };
      });
    },

    applyState(event: ConversationStateEvent): void {
      const agentId = event.agent_id;
      const next: ConversationMeta = {
        liveState: event.live_state ?? null,
        queuedMessage: event.queued_message ?? null,
      };
      store.setState((state) => {
        const prev = state.conversations.meta[agentId];
        // Identity-preserve when nothing changed so memoised consumers skip re-render.
        if (
          prev !== undefined &&
          prev.liveState === next.liveState &&
          prev.queuedMessage === next.queuedMessage
        ) {
          return state;
        }
        return {
          conversations: {
            ...state.conversations,
            meta: { ...state.conversations.meta, [agentId]: next },
          },
        };
      });
    },

    async sendKey(agentId: string, key: string, literal: boolean, enter = false): Promise<void> {
      try {
        await submitCommand(bus, 'agent.send_key', {
          agent_id: agentId,
          key,
          literal,
          enter,
        });
      } catch (error: unknown) {
        // Fire-and-forget, same policy as send(): the pane mirror shows the dialog's true state.
        void error;
      }
    },

    clearTranscript(agentId: string): void {
      store.setState((state) => {
        const blocks = state.conversations.transcripts[agentId] ?? [];
        // The floor is the max numeric block id present now. Blocks with no/non-numeric id are
        // ignored (they can't be compared); an empty transcript yields a 0 floor (a no-op filter).
        let maxId = 0;
        for (const block of blocks) {
          const n = block.id === null || block.id === undefined ? Number.NaN : Number(block.id);
          if (Number.isFinite(n) && n > maxId) {
            maxId = n;
          }
        }
        return {
          conversations: {
            ...state.conversations,
            clearedFloors: { ...state.conversations.clearedFloors, [agentId]: maxId },
          },
        };
      });
    },

    async interrupt(agentId: string): Promise<void> {
      try {
        toastStore.getState().push('interrupt → queued message will send', { ttlMs: 5000 });
        await submitCommand(bus, 'agent.interrupt', { agent_id: agentId });
      } catch (error: unknown) {
        toastStore.getState().push('interrupt failed', { severity: 'error', ttlMs: 8000 });
        void error;
      }
    },

    setActivePaneAgentId(agentId: string | null): void {
      store.setState((state) => ({
        conversations: { ...state.conversations, activePaneAgentId: agentId },
      }));
    },

    setChatPaneOpen(agentId: string, open: boolean): void {
      store.setState((state) => {
        const next = new Map(state.conversations.paneOverrides);
        next.set(agentId, open);
        return { conversations: { ...state.conversations, paneOverrides: next } };
      });
    },

    toggleChatPane(agentId: string, currentlyOpen: boolean): void {
      store.setState((state) => {
        const next = new Map(state.conversations.paneOverrides);
        next.set(agentId, !currentlyOpen);
        return { conversations: { ...state.conversations, paneOverrides: next } };
      });
    },

    setPaneViewMode(agentId: string, mode: ChatViewMode): void {
      store.setState((state) => ({
        conversations: {
          ...state.conversations,
          paneViewModes: { ...state.conversations.paneViewModes, [agentId]: mode },
        },
      }));
    },

    cyclePaneViewMode(agentId: string): void {
      store.setState((state) => {
        const settings = state.settings;
        const current = state.conversations.paneViewModes[agentId] ?? settings.defaultChatViewMode;
        const next = CHAT_VIEW_CYCLE[current];
        return {
          conversations: {
            ...state.conversations,
            paneViewModes: { ...state.conversations.paneViewModes, [agentId]: next },
          },
        };
      });
    },
  };
}

/** Cycle order (TUIchat-3): verbose → condensed → tmux → verbose. */
const CHAT_VIEW_CYCLE: Readonly<Record<ChatViewMode, ChatViewMode>> = {
  verbose: 'condensed',
  condensed: 'tmux',
  tmux: 'verbose',
};
