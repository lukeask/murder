/** Direct application-command dispatch tests. */

import { describe, expect, it } from 'vitest';
import { FakeApplicationClient } from '../../src/application/FakeApplicationClient.js';
import { submitCommand } from '../../src/store/commandSubmit.js';

describe('submitCommand', () => {
  it('sends the generated command name and its payload directly', async () => {
    const client = new FakeApplicationClient();
    client.stubCommand('ticket.quick_create', { ticket_id: 'T-9', handled: true });

    await expect(submitCommand(client, 'ticket.quick_create', { title: 'hi' })).resolves.toEqual({
      ticket_id: 'T-9',
      handled: true,
    });
    expect(client.commandCalls).toEqual([
      { name: 'ticket.quick_create', params: { title: 'hi' } },
    ]);
    expect(client.queryCalls).toEqual([]);
  });

  it('returns the direct command result without client-side polling', async () => {
    const client = new FakeApplicationClient();
    client.stubCommand('agent.message', { accepted: true });

    await expect(
      submitCommand(client, 'agent.message', { agent_id: 'a', message: 'hello' }),
    ).resolves.toEqual({ accepted: true });
    expect(client.queryCalls).toEqual([]);
  });

  it('preserves a command failure from the application client', async () => {
    const client = new FakeApplicationClient();
    client.stubCommand('agent.stop', () => {
      throw new Error('not allowed');
    });

    await expect(submitCommand(client, 'agent.stop', { agent_id: 'a' })).rejects.toThrow('not allowed');
  });
});
