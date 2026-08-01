/**
 * conversations.send — optimistic pending shadow turns + command ack status updates.
 */

import { beforeEach, describe, expect, it } from 'vitest';
import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import {
  applyConversationsSnapshot,
  type ConversationsSnapshotReply,
} from '@murder/ui-core/store/conversations/conversationsActions.js';
import { createAppStore, type AppStoreApi } from '@murder/ui-core/store/store.js';
import { toastStore } from '@murder/ui-core/store/toast/toastStore.js';

describe('conversations.send optimistic pending', () => {
  let bus: FakeApplicationClient;
  let store: AppStoreApi;

  beforeEach(() => {
    bus = new FakeApplicationClient();
    store = createAppStore(bus).store;
    toastStore.getState().clear();
  });

  it('adds a pending shadow turn immediately and marks accepted on ack', async () => {
    bus.stubCommand('agent.message', async (params) => {
      expect(params['client_message_id']).toEqual(expect.any(String));
      expect(params['message']).toBe('hi there');
      // Pending should already be visible before the command resolves.
      const pending = store.getState().conversations.pendingByAgent['crow-1'];
      expect(pending).toHaveLength(1);
      expect(pending?.[0]?.status).toBe('sending');
      expect(pending?.[0]?.text).toBe('hi there');
      return { handled: true, queued: false };
    });

    await store.getState().actions.conversations.send('crow-1', 'hi there');

    const pending = store.getState().conversations.pendingByAgent['crow-1'];
    expect(pending).toHaveLength(1);
    expect(pending?.[0]?.status).toBe('accepted');
  });

  it('marks queued when the command reports queued', async () => {
    bus.stubCommand('agent.message', async () => ({ handled: true, queued: true }));
    await store.getState().actions.conversations.send('crow-1', 'later');
    expect(store.getState().conversations.pendingByAgent['crow-1']?.[0]?.status).toBe('queued');
  });

  it('marks failed on definitive rejection', async () => {
    bus.stubCommand('agent.message', async () => ({
      ok: false,
      handled: false,
      error: 'agent is still starting; try again shortly',
    }));
    await store.getState().actions.conversations.send('crow-1', 'too soon');
    expect(store.getState().conversations.pendingByAgent['crow-1']?.[0]?.status).toBe('failed');
  });

  it('marks unknown on transport failure', async () => {
    bus.stubCommand('agent.message', async () => {
      throw new Error('timeout');
    });
    await store.getState().actions.conversations.send('crow-1', 'maybe');
    expect(store.getState().conversations.pendingByAgent['crow-1']?.[0]?.status).toBe('unknown');
  });

  it('drops pending when a snapshot confirms the client_message_id', async () => {
    bus.stubCommand('agent.message', async () => ({
      handled: true,
      queued: false,
    }));
    await store.getState().actions.conversations.send('crow-1', 'confirm me');
    const clientId = store.getState().conversations.pendingByAgent['crow-1']?.[0]?.clientId;
    expect(clientId).toEqual(expect.any(String));

    const reply: ConversationsSnapshotReply = {
      conversations: [
        {
          conversation_id: 'crow-1',
          agent_id: 'crow-1',
          harness: null,
          model: null,
          harness_session_id: null,
          live_state: null,
          chunk_summaries: [],
          status: 'in_progress',
          blocks: [
            {
              id: 42,
              conversation_id: 'crow-1',
              ordinal: 1,
              kind: 'user',
              payload: { type: 'user', text: 'confirm me', client_message_id: clientId },
              sealed: true,
              service_received_at: '2026-01-01T00:00:00Z',
            },
          ],
        },
      ],
      as_of: '2026-01-01T00:00:00Z',
      invalidation_key: 'k',
    };
    applyConversationsSnapshot(store, reply);

    expect(store.getState().conversations.pendingByAgent['crow-1']).toBeUndefined();
    expect(store.getState().conversations.transcripts['crow-1']).toHaveLength(1);
  });

  it('retries a failed pending send with the same clientId', async () => {
    const calls: unknown[] = [];
    bus.stubCommand('agent.message', async (params) => {
      calls.push(params['client_message_id']);
      if (calls.length === 1) {
        return { ok: false, handled: false, error: 'nope' };
      }
      return { handled: true, queued: false };
    });

    await store.getState().actions.conversations.send('crow-1', 'retry me');
    const clientId = store.getState().conversations.pendingByAgent['crow-1']?.[0]?.clientId;
    expect(clientId).toEqual(expect.any(String));
    expect(store.getState().conversations.pendingByAgent['crow-1']?.[0]?.status).toBe('failed');

    await store.getState().actions.conversations.retryPending('crow-1', clientId as string);
    expect(calls).toEqual([clientId, clientId]);
    expect(store.getState().conversations.pendingByAgent['crow-1']?.[0]?.status).toBe('accepted');
  });
});
