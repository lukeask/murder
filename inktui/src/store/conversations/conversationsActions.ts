/**
 * Conversations actions — the only code that calls the bus for chat operations (rule 3).
 *
 * Three actions:
 *  1. `refresh()` — explicit boot-prime pull. Calls `state.conversations_snapshot` to hydrate the
 *     transcripts map on connect (cold-start, before any `conversation.block` events arrive). The
 *     reply is a map of agentId → block array; each block is parsed through `parseBlock` exactly as
 *     `applyBlock` does, so the seam is consistent. Errors are swallowed into the `conversations`
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
import type { ConversationBlockEvent } from '../../bus/protocol.js';
import { submitCommand } from '../commandSubmit.js';
import type { AppStore } from '../store.js';
import { toastStore } from '../toast/toastStore.js';
import { parseBlock, type ConversationBlock } from './conversationsSlice.js';

/**
 * Declares the conversations read RPC via declaration merging rather than editing the frozen C1 bus
 * files. `state.conversations_snapshot` is the bus-contract name (`domain.verb`, mirrors Python
 * `RuntimeClient.get_conversations_snapshot`). Called on connect to prime the transcripts map so a
 * cold-start service (no `conversation.block` events yet) paints populated chat panes immediately.
 */
declare module '../../bus/BusClient.js' {
  interface RpcMethods {
    /**
     * Fetch all agent transcripts as a snapshot. Re-pulled on connect (boot-prime); individual
     * block updates arrive via `conversation.block` events thereafter. The reply is a map of
     * agentId → block rows (same wire shape as `ConversationBlockEvent.block`).
     */
    'state.conversations_snapshot': {
      params: Record<string, never>;
      result: ConversationsSnapshotReply;
    };
  }
}

/**
 * The `state.conversations_snapshot` reply. Mirrors the service's `ConversationsSnapshot` DTO
 * (`murder/app/service/client_api.py`). `transcripts` is a map from agentId to an ordered array of
 * raw block rows (the same wire shape as `ConversationBlockEvent.block`), so `parseBlock` applies
 * unchanged.
 */
export interface ConversationsSnapshotReply {
  /** Per-agent block rows, keyed by agentId. Only agents with at least one block are included. */
  transcripts: Readonly<Record<string, readonly Record<string, unknown>[]>>;
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
   * Explicitly set the active chat pane. Called by the CrowChatPanel when the user navigates
   * between panes or the "keep pane active" path fires. Does not call the bus.
   * C11 seam: this slot is here for ctrl+s "keep pane active"; the full starring/prefs system
   * (tui.save_favorites) is C11's responsibility.
   */
  setActivePaneAgentId(agentId: string | null): void;
}

export function createConversationsActions(
  bus: BusClient,
  store: StoreApi<AppStore>,
): ConversationsActions {
  return {
    async refresh(): Promise<void> {
      try {
        const reply = await bus.rpc('state.conversations_snapshot', {});
        // Project the wire block rows through parseBlock — the same normalisation as applyBlock
        // uses for live events, so the transcript shape is always consistent.
        const parsed: Record<string, readonly ConversationBlock[]> = {};
        for (const [agentId, blocks] of Object.entries(reply.transcripts)) {
          parsed[agentId] = blocks.map(parseBlock);
        }
        store.setState((state) => ({
          conversations: {
            ...state.conversations,
            transcripts: { ...state.conversations.transcripts, ...parsed },
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
          toastStore.getState().push(errorText, { severity: 'error', ttlMs: 6000 });
          return;
        }
        if (result['queued'] === true) {
          toastStore.getState().push('message queued (crow busy)', { ttlMs: 3000 });
        } else {
          toastStore.getState().push(`→ ${agentId}`, { ttlMs: 2000 });
        }
        // Keep the pane for this agent active after sending.
        store.setState((state) => ({
          conversations: { ...state.conversations, activePaneAgentId: agentId },
        }));
      } catch (error: unknown) {
        // Swallow: send is fire-and-forget from the UI perspective.
        // The bus-level error policy (timeout/drop) is in UdsBusClient.
        // A future retry/status mechanism belongs in a dedicated action.
        void error;
      }
    },

    applyBlock(event: ConversationBlockEvent): void {
      const agentId = event.agent_id;
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

    setActivePaneAgentId(agentId: string | null): void {
      store.setState((state) => ({
        conversations: { ...state.conversations, activePaneAgentId: agentId },
      }));
    },
  };
}
