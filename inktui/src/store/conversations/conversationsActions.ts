/**
 * Conversations actions — the only code that calls the bus for chat operations (rule 3).
 *
 * Three actions:
 *  1. `refresh()` — explicit user/mount pull. Calls `state.conversations_snapshot` to hydrate the
 *     transcripts map outside the startup hydrate path. The
 *     reply is a list of `ConversationSummary` entries (in-progress conversations); each entry's
 *     `agent_id` becomes the key and its `blocks` are parsed through `parseBlock` (same wire shape
 *     as the event block, so the seam is consistent). Errors are swallowed into the `conversations`
 *     slice (future: add an `error` field when needed).
 *
 *  2. `send(agentId, message)` — the sole sender of chat messages. `agent.message` is an
 *     orchestrator command kind (not a standalone RPC), so this routes through the live
 *     `command.submit` choke point ({@link ../commandSubmit.js}). Routes to the agent identified by
 *     `agentId`; the discriminated-union identity (deriving the right agentId) lives in the
 *     selectors/transcript pane, NOT here (rule 2). This action receives the resolved agentId from its
 *     caller, never parses a conversation_id (rule 1 / anti-pattern).
 *
 *  3. Projection invalidations refresh the authoritative conversation snapshot through `store.ts`.
 *
 * `agent.message` is dispatched as an orchestrator command kind via `command.submit` (the live
 * write seam) rather than as a direct RPC — see {@link ../commandSubmit.js}. The discriminated-union
 * agent identity is resolved by the caller (rule 2); this action just submits the command.
 */

import type { StoreApi } from 'zustand';
import type { ApplicationClient } from '../../application/ApplicationClient.js';
import { asQueryResult } from '../../application/resultCast.js';
import { stageTranscriptFocusId } from '../../input/focusIds.js';
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
 * cold-start service paints populated transcript panes immediately.
 */

/**
 * One block as it appears inside `ConversationSummary.blocks` (the `ConversationBlockSummary` DTO,
 * `murder/app/protocol/read_models.py`).
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
 * `ConversationChunkSummary` DTO, `murder/app/protocol/read_models.py`). `dto_to_wire` preserves the
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
 * `murder/app/protocol/read_models.py`). Only `in_progress` conversations are included.
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
 * (`murder/app/protocol/read_models.py`). `conversations` is a list of `ConversationSummary` entries
 * (only `in_progress` conversations), each carrying the full block history for that agent.
 * Keying is by `agent_id` (CONTRACT ASSUMPTION: one active conversation per agent).
 */
export interface ConversationsSnapshotReply {
  conversations: readonly ConversationSummaryDto[];
  /** ISO-8601 datetime string — when the snapshot was taken. */
  as_of: string;
  invalidation_key: string;
}

interface ProjectedConversationsSnapshot {
  readonly transcripts: Record<string, readonly ConversationBlock[]>;
  readonly meta: Record<string, ConversationMeta>;
  readonly chunkSummaries: Record<string, readonly ChunkSummary[]>;
}

export function projectConversationsSnapshot(
  reply: ConversationsSnapshotReply,
): ProjectedConversationsSnapshot {
  const transcripts: Record<string, readonly ConversationBlock[]> = {};
  const meta: Record<string, ConversationMeta> = {};
  const chunkSummaries: Record<string, readonly ChunkSummary[]> = {};
  for (const conv of reply.conversations) {
    transcripts[conv.agent_id] = conv.blocks.map((b) =>
      parseBlock(b as unknown as Record<string, unknown>),
    );
    meta[conv.agent_id] = {
      liveState: conv.live_state ?? null,
      queuedMessage: conv.queued_message ?? null,
    };
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
  return { transcripts, meta, chunkSummaries };
}

export function applyConversationsSnapshot(
  store: StoreApi<AppStore>,
  reply: ConversationsSnapshotReply,
): void {
  const projected = projectConversationsSnapshot(reply);
  store.setState((state) => ({
    conversations: {
      ...state.conversations,
      transcripts: projected.transcripts,
      meta: projected.meta,
      chunkSummaries: projected.chunkSummaries,
    },
  }));
}

/** The conversations actions, bound to one `ApplicationClient` + store handle. */
export interface ConversationsActions {
  /**
   * Explicit refresh: pull all agent transcripts from `state.conversations_snapshot` and populate
   * the transcripts map. Startup hydration applies the same snapshot shape through
   * `applyConversationsSnapshot`; this action remains for explicit refresh/mount paths.
   *
   * Errors are swallowed (fire-and-forget from the priming path; transcripts remain empty rather
   * than crashing; the next projection invalidation retries the authoritative refresh).
   */
  refresh(): Promise<void>;

  /**
   * Send a message to the agent identified by `agentId` via `agent.message`.
   * The sole bus caller for chat sends — rule 3. The caller (transcript pane)
   * resolves the agentId from the discriminated-union identity BEFORE calling this action.
   * No conversation_id parsing, no string-prefix matching — ever.
   *
   * On success: sets `activePaneAgentId` to `agentId` ("keep pane active" after send).
   * On failure: the action swallows the rejection (logs — callers treat send as fire-and-forget
   * from the UI perspective). The bus-level error policy (timeouts) is the implementation's.
   */
  send(agentId: string, message: string): Promise<void>;

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
   * Explicitly set the active transcript pane. Called by the transcript pane when the user navigates
   * between panes or the "keep pane active" path fires. Does not call the bus.
   * C11 seam: this slot is here for ctrl+s "keep pane active"; the full starring/prefs system
   * (tui.save_favorites) is C11's responsibility.
   */
  setActivePaneAgentId(agentId: string | null): void;

  /**
   * Explicitly open or close a transcript pane (item 9b). Writes a `paneOverrides` entry that layers over
   * the favorites-derived default — so `open=true` forces a non-favorited agent's pane visible, and
   * `open=false` hides a default-favorited one. No bus call. Used by `spawnRogue`'s auto-open (9e).
   */
  setTranscriptPaneOpen(agentId: string, open: boolean): void;

  /**
   * Toggle a transcript pane open/closed (item 9c). `currentlyOpen` is the pane's CURRENT effective open
   * state (the caller computes it via `selectOpenTranscriptPanes`, which merges the favorites default with
   * the existing override); the action records the override that flips it. No bus call.
   */
  toggleTranscriptPane(agentId: string, currentlyOpen: boolean): void;

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

  /** Mark a pane/panel as activated for layout reap aging. Priority 0 panes remain unreapable in
   * layout; this only tracks relative age for positive-priority requests. */
  activatePane(paneId: string | null): void;
}

function activatePaneReapAges(
  current: ReadonlyMap<string, number>,
  paneId: string | null,
): ReadonlyMap<string, number> {
  if (paneId === null) {
    return current;
  }
  const next = new Map<string, number>();
  for (const [id, age] of current) {
    next.set(id, id === paneId ? 0 : age > 0 ? age + 1 : 1);
  }
  next.set(paneId, 0);
  return next;
}

export function createConversationsActions(
  bus: ApplicationClient,
  store: StoreApi<AppStore>,
): ConversationsActions {
  // Per-call request token — guards against a stale reply replacing the authoritative set when a
  // reconnect re-prime overlaps two refreshes (same pattern as listSlice.ts / transitActions).
  let seq = 0;
  return {
    async refresh(): Promise<void> {
      const token = ++seq;
      try {
        const reply = await bus.query('conversations.get', {});
        if (token !== seq) return;
        // REPLACE, do not union: the snapshot is authoritative for the in-progress set. A merge
        // (`{...old, ...parsed}`) would keep an agent whose conversation has since ENDED (absent
        // from the snapshot) forever — accumulating ghost panes/dead transcripts across reconnects.
        // The map is rebuilt from exactly the snapshot's conversations.
        applyConversationsSnapshot(
          store,
          asQueryResult<'conversations.get', ConversationsSnapshotReply>(reply),
        );
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
          conversations: {
            ...state.conversations,
            activePaneAgentId: agentId,
            paneReapAges: activatePaneReapAges(
              state.conversations.paneReapAges,
              stageTranscriptFocusId(agentId),
            ),
          },
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
        conversations: {
          ...state.conversations,
          activePaneAgentId: agentId,
          paneReapAges: activatePaneReapAges(
            state.conversations.paneReapAges,
            agentId === null ? null : stageTranscriptFocusId(agentId),
          ),
        },
      }));
    },

    activatePane(paneId: string | null): void {
      store.setState((state) => ({
        conversations: {
          ...state.conversations,
          paneReapAges: activatePaneReapAges(state.conversations.paneReapAges, paneId),
        },
      }));
    },

    setTranscriptPaneOpen(agentId: string, open: boolean): void {
      store.setState((state) => {
        const next = new Map(state.conversations.paneOverrides);
        next.set(agentId, open);
        return { conversations: { ...state.conversations, paneOverrides: next } };
      });
    },
    toggleTranscriptPane(agentId: string, currentlyOpen: boolean): void {
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
