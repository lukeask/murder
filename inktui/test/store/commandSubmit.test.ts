/**
 * `submitCommand` helper tests — the orchestrator command-bus write protocol (F2).
 *
 * Covers the submit-then-poll contract the live `command.submit` / `command.status` pair defines:
 *  - a `'done'` status resolves with the JSON-parsed `result_json` worker reply;
 *  - the submit carries `target_worker: 'orchestrator'` + the given kind/payload;
 *  - a `'failed'` status rejects with the worker's `last_error`;
 *  - a missing `command_id` rejects.
 */

import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { submitCommand } from '../../src/store/commandSubmit.js';

describe('submitCommand', () => {
  it('submits to the orchestrator and resolves with the parsed result_json on done', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
    fake.stubRpc('command.status', {
      ok: true,
      status: 'done',
      result_json: JSON.stringify({ ticket_id: 'T-9', handled: true }),
    });

    const result = await submitCommand(fake, 'ticket.quick_create', { title: 'hi' });

    expect(result).toEqual({ ticket_id: 'T-9', handled: true });
    const submit = fake.rpcCalls.find((c) => c.method === 'command.submit');
    expect(submit?.params).toMatchObject({
      target_worker: 'orchestrator',
      kind: 'ticket.quick_create',
      payload: { title: 'hi' },
    });
  });

  it('returns {} when the worker reply has no body', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
    fake.stubRpc('command.status', { ok: true, status: 'done', result_json: null });

    await expect(
      submitCommand(fake, 'agent.message', { agent_id: 'a', message: 'm' }),
    ).resolves.toEqual({});
  });

  it('rejects with the worker last_error on a failed command', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
    fake.stubRpc('command.status', { ok: true, status: 'failed', last_error: 'boom' });

    await expect(
      submitCommand(fake, 'crow.spawn_rogue', { harness: 'claude', model: 'sonnet' }),
    ).rejects.toThrow('boom');
  });

  it('rejects when command.submit returns no command_id', async () => {
    const fake = new FakeBusClient();
    fake.stubRpc('command.submit', { ok: false, command_id: '' });

    await expect(submitCommand(fake, 'ticket.quick_create', { title: 'x' })).rejects.toThrow(
      'no command_id',
    );
  });
});
