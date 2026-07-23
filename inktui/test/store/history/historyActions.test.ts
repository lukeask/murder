/**
 * History actions tests — `refresh()` projects the wire snapshot into rows, and `dismiss()`
 * optimistically marks the row dismissed then submits the `history.dismiss` orchestrator command.
 * Driven by {@link FakeApplicationClient}.
 */

import { describe, expect, it } from 'vitest';
import { FakeApplicationClient } from '../../../src/application/FakeApplicationClient.js';
import { createAppStore } from '../../../src/store/store.js';

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function setup() {
  const fake = new FakeApplicationClient();
  fake.stubQuery('roster.get', { invalidation_key: 'iv', sessions: [] });
  fake.stubQuery('schedule.get', {
    invalidation_key: 'iv',
    active_tickets: [],
    recent_done_tickets: [],
    archived_tickets: [],
    usage_gauges: [],
  });
  const { store, dispose } = createAppStore(fake);
  return { fake, store, dispose };
}

describe('history actions', () => {
  it('refresh projects the wire snapshot into rows', async () => {
    const { fake, store } = setup();
    fake.stubQuery('history.list', {
      invalidation_key: 'iv-h',
      items: [
        {
          item_id: 'crow-t1:0',
          text: 'do the thing',
          target: 'crow-t1',
          conversation_id: 'conv-crow-t1',
          ts: '2026-06-10T00:00:00',
          status: 'open',
          harness: 'claude_code',
          conversation_status: 'complete',
          resumable: true,
        },
      ],
    });

    await store.getState().actions.history.refresh();

    const history = store.getState().history;
    expect(history.status).toBe('ready');
    expect(history.rows).toEqual([
      {
        itemId: 'crow-t1:0',
        text: 'do the thing',
        target: 'crow-t1',
        conversationId: 'conv-crow-t1',
        ts: '2026-06-10T00:00:00',
        status: 'open',
        harness: 'claude_code',
        conversationStatus: 'complete',
        resumable: true,
      },
    ]);
  });

  it('dismiss optimistically marks the row then submits history.dismiss', async () => {
    const { fake, store } = setup();
    fake.stubQuery('history.list', {
      invalidation_key: 'iv-h',
      items: [
        {
          item_id: 'collaborator:0',
          text: 'dismiss me',
          target: 'collaborator',
          conversation_id: 'conv-collaborator',
          ts: '2026-06-10T00:00:00',
          status: 'open',
          harness: null,
          conversation_status: 'in_progress',
          resumable: false,
        },
      ],
    });
    fake.stubAllCommands({ ok: true, command_id: 'cmd-1' });
    fake.stubQuery('command.get', {
      ok: true,
      status: 'done',
      result_json: JSON.stringify({ item_id: 'collaborator:0', status: 'dismissed' }),
    });

    await store.getState().actions.history.refresh();
    expect(store.getState().history.rows[0]?.status).toBe('open');

    await store.getState().actions.history.dismiss('collaborator:0');
    await flush();

    // The row is optimistically marked dismissed.
    expect(store.getState().history.rows[0]?.status).toBe('dismissed');
    // And the orchestrator command was submitted with the item id.
    const submit = fake.commandCalls.find((c) => c.name === 'history.dismiss');
    expect(submit?.params).toMatchObject({ item_id: 'collaborator:0' });
  });

  it('resumeConversation submits agent.resume_from_history with the conversation id', async () => {
    const { fake, store } = setup();
    fake.stubAllCommands({ ok: true, command_id: 'cmd-r' });
    fake.stubQuery('command.get', {
      ok: true,
      status: 'done',
      result_json: JSON.stringify({ handled: true, agent_id: 'crow-rogue-resumed' }),
    });

    await store.getState().actions.history.resumeConversation('crow-t1');

    const submit = fake.commandCalls.find((c) => c.name === 'agent.resume_from_history');
    expect(submit?.params).toMatchObject({ conversation_id: 'crow-t1' });
  });

  it('resumeConversation swallows backend rejection (does not throw)', async () => {
    const { fake, store } = setup();
    fake.stubAllCommands({ ok: true, command_id: 'cmd-r' });
    fake.stubQuery('command.get', {
      ok: true,
      status: 'failed',
      last_error: 'resume is only supported for Claude Code sessions',
    });

    // Must resolve (not reject): the action surfaces the error as a toast.
    await expect(
      store.getState().actions.history.resumeConversation('crow-cursor'),
    ).resolves.toBeUndefined();
  });
});
