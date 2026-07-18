/**
 * `submitCommand` helper tests — the orchestrator command-bus write protocol (F2).
 *
 * Covers the submit-then-poll contract the live `orchestration.execute` / `command.get` pair defines:
 *  - a `'done'` status resolves with the JSON-parsed `result_json` worker reply;
 *  - the generated command carries the given orchestration kind/payload;
 *  - a `'failed'` status rejects with the worker's `last_error`;
 *  - a missing `command_id` rejects.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { submitCommand } from '../../src/store/commandSubmit.js';

// Mirror of the source constants (`commandSubmit.ts`). The helper exposes no injectable clock seam
// (another agent owns that source file; we deliberately do not add one here), so the poll/timeout
// branches are driven by Vitest fake timers advancing the real `setTimeout`-based `delay`. If these
// constants change in the source, update them here. NOTE/GAP: because there is no exported delay
// seam, these tests reach into timing via fake timers rather than an injected clock — the cleaner
// fix (an injectable clock) is left to the source owner.
const POLL_INTERVAL_MS = 100;
const MAX_POLLS = 600;

describe('submitCommand', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('submits to the orchestrator and resolves with the parsed result_json on done', async () => {
    const fake = new FakeBusClient();
    fake.stubCommand('orchestration.execute', { ok: true, command_id: 'cmd-1' });
    fake.stubQuery('command.get', {
      ok: true,
      status: 'done',
      result_json: JSON.stringify({ ticket_id: 'T-9', handled: true }),
    });

    const result = await submitCommand(fake, 'ticket.quick_create', { title: 'hi' });

    expect(result).toEqual({ ticket_id: 'T-9', handled: true });
    const submit = fake.commandCalls.find((c) => c.name === 'orchestration.execute');
    expect(submit?.params).toMatchObject({
      kind: 'ticket.quick_create',
      payload: { title: 'hi' },
    });
  });

  it('returns {} when the worker reply has no body', async () => {
    const fake = new FakeBusClient();
    fake.stubCommand('orchestration.execute', { ok: true, command_id: 'cmd-1' });
    fake.stubQuery('command.get', { ok: true, status: 'done', result_json: null });

    await expect(
      submitCommand(fake, 'agent.message', { agent_id: 'a', message: 'm' }),
    ).resolves.toEqual({});
  });

  it('rejects with the worker last_error on a failed command', async () => {
    const fake = new FakeBusClient();
    fake.stubCommand('orchestration.execute', { ok: true, command_id: 'cmd-1' });
    fake.stubQuery('command.get', { ok: true, status: 'failed', last_error: 'boom' });

    await expect(
      submitCommand(fake, 'crow.spawn_rogue', { harness: 'claude', model: 'sonnet' }),
    ).rejects.toThrow('boom');
  });

  it('rejects when orchestration.execute returns no command_id', async () => {
    const fake = new FakeBusClient();
    fake.stubCommand('orchestration.execute', { ok: false, command_id: '' });

    await expect(submitCommand(fake, 'ticket.quick_create', { title: 'x' })).rejects.toThrow(
      'no command_id',
    );
  });

  it('re-polls command.get through running/queued until a terminal status', async () => {
    // The whole reason this helper is more than one RPC: a command starts `queued`/`running` and is
    // polled until it reaches `done`. Drive that path explicitly — earlier tests all returned a
    // terminal status on the FIRST poll, so the wait-and-repoll loop had zero coverage.
    vi.useFakeTimers();
    const fake = new FakeBusClient();
    fake.stubCommand('orchestration.execute', { ok: true, command_id: 'cmd-1' });
    const statuses = [
      { ok: true, status: 'queued' as const },
      { ok: true, status: 'running' as const },
      { ok: true, status: 'done' as const, result_json: JSON.stringify({ ticket_id: 'T-7' }) },
    ];
    let poll = 0;
    fake.stubQuery('command.get', () => {
      const status = statuses[Math.min(poll++, statuses.length - 1)];
      if (status === undefined) {
        throw new Error('missing command status fixture');
      }
      return status;
    });

    const pending = submitCommand(fake, 'ticket.quick_create', { title: 'hi' });
    // Two non-terminal polls each wait one POLL_INTERVAL_MS before the third (done) poll resolves.
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 2);

    await expect(pending).resolves.toEqual({ ticket_id: 'T-7' });
    const statusCalls = fake.queryCalls.filter((c) => c.name === 'command.get');
    expect(statusCalls).toHaveLength(3);
  });

  it('rejects with a timeout after MAX_POLLS non-terminal polls', async () => {
    // A command that never reaches a terminal state must not hang forever: after MAX_POLLS the helper
    // rejects with `<kind> timed out`. With real timers this would take ~60s; fake timers make it
    // instant while still exercising the bounded loop.
    vi.useFakeTimers();
    const fake = new FakeBusClient();
    fake.stubCommand('orchestration.execute', { ok: true, command_id: 'cmd-1' });
    fake.stubQuery('command.get', { ok: true, status: 'running' });

    const pending = submitCommand(fake, 'crow.spawn_rogue', { harness: 'claude', model: 'sonnet' });
    const assertion = expect(pending).rejects.toThrow('crow.spawn_rogue timed out');
    // Advance past every inter-poll delay so the loop exhausts its budget.
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * (MAX_POLLS + 1));
    await assertion;

    const statusCalls = fake.queryCalls.filter((c) => c.name === 'command.get');
    expect(statusCalls).toHaveLength(MAX_POLLS);
  });
});
