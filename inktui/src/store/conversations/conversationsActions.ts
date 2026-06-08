/**
 * Conversations actions — the only code that calls the bus for chat operations (rule 3).
 *
 * Two actions:
 *  1. `send(agentId, message)` — the sole caller of `bus.rpc('agent.message', …)`. Routes to the
 *     agent identified by `agentId`; the discriminated-union identity (deriving the right agentId)
 *     lives in the selectors/CrowChatPanel, NOT here (rule 2). This action receives the resolved
 *     agentId from its caller, never parses a conversation_id (rule 1 / anti-pattern).
 *
 *  2. `applyBlock(event)` — pure setState, no bus call. Called by the second `bus.subscribe` in
 *     `store.ts` on each `conversation.block` event. Handles both `block-appended` (push) and
 *     `block-updated` (replace trailing block with matching id). Ref-swaps only the affected
 *     agent's transcript array — sibling agents keep identity.
 *
 * `agent.message` is ALREADY in `RpcMethods` (BusClient.ts line 38):
 *   `'agent.message': { params: { agent_id: string; message: string }; result: RpcPayload }`
 * No `declare module` augmentation needed — just call it directly.
 */

import type { StoreApi } from 'zustand';
import type { BusClient } from '../../bus/BusClient.js';
import type { ConversationBlockEvent } from '../../bus/protocol.js';
import type { AppStore } from '../store.js';
import { parseBlock } from './conversationsSlice.js';

/** The conversations actions, bound to one `BusClient` + store handle. */
export interface ConversationsActions {
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
    async send(agentId: string, message: string): Promise<void> {
      try {
        // `agent.message` is already in RpcMethods — no declare module needed.
        await bus.rpc('agent.message', { agent_id: agentId, message });
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
