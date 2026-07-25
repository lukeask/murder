/**
 * Creation dialogs — open + submit happy paths for New Ticket, New Plan, and Spawn Rogue.
 * Asserts each dialog fires the shared `@core` dialog/spawn action command on the FakeApplicationClient.
 */

import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NewTicketDialog } from '../src/components/modals/NewTicketDialog.js';
import { NewPlanDialog } from '../src/components/modals/NewPlanDialog.js';
import { SpawnRogueDialog } from '../src/components/modals/SpawnRogueDialog.js';
import { makeStore, renderWithStore } from './helpers.js';

afterEach(cleanup);

describe('NewTicketDialog', () => {
  it('submits title via ticket.quick_create', async () => {
    const { bus } = makeStore();
    bus.stubCommand('ticket.quick_create', {
      handled: true,
      ticket_id: 't-001',
      title: 'fix the bug',
    });
    const onClose = vi.fn();
    renderWithStore(<NewTicketDialog open onClose={onClose} />, { bus });

    expect(screen.getByRole('dialog')).toBeTruthy();
    const input = screen.getByLabelText('Title');
    fireEvent.change(input, { target: { value: 'fix the bug' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(bus.commandCalls).toEqual([
      { name: 'ticket.quick_create', params: { title: 'fix the bug' } },
    ]);
  });
});

describe('NewPlanDialog', () => {
  it('submits auto-named plan via plan.create', async () => {
    const { bus } = makeStore();
    bus.stubCommand('plan.create', {
      handled: true,
      ok: true,
      plan_name: 'auto-named',
      agent_id: 'planner-auto-named',
    });
    const onClose = vi.fn();
    renderWithStore(<NewPlanDialog open onClose={onClose} />, { bus });

    expect(screen.getByRole('dialog')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Message'), {
      target: { value: 'do the thing' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(bus.commandCalls.length).toBe(1);
    expect(bus.commandCalls[0]).toMatchObject({
      name: 'plan.create',
      params: { auto_name: true, body: 'do the thing', message: 'do the thing' },
    });
  });
});

describe('SpawnRogueDialog', () => {
  it('submits spawn via crow.spawn_rogue after loading options', async () => {
    const { bus } = makeStore();
    bus.stubQuery('harness_models.list', {
      models: {
        claude_code: [
          { id: 'sonnet', label: 'Sonnet' },
          { id: 'opus', label: 'Opus' },
        ],
      },
      as_of: null,
    });
    bus.stubQuery('worktrees.list', { ok: true, entries: [] });
    bus.stubQuery('spawn_favorites.get', { favorites: [] });
    bus.stubCommand('crow.spawn_rogue', {
      handled: true,
      agent_id: 'rogue-1',
    });
    const onClose = vi.fn();
    renderWithStore(<SpawnRogueDialog open onClose={onClose} />, { bus });

    expect(screen.getByRole('dialog')).toBeTruthy();
    await waitFor(() => expect(screen.getByLabelText('Harness')).toBeTruthy());

    fireEvent.click(screen.getByRole('button', { name: 'Spawn' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const spawnCall = bus.commandCalls.find((c) => c.name === 'crow.spawn_rogue');
    expect(spawnCall).toBeTruthy();
    expect(spawnCall?.params).toMatchObject({
      harness: 'claude_code',
      model: 'sonnet',
      effort: 'medium',
    });
  });
});
