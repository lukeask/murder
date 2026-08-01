/**
 * Creation dialogs — open + submit happy paths for New Ticket, New Plan, Spawn Rogue,
 * New Report, and Note capture. Asserts each dialog fires the shared action command on the
 * FakeApplicationClient.
 */

import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { NewTicketDialog } from '../src/components/modals/NewTicketDialog.js';
import { NewPlanDialog } from '../src/components/modals/NewPlanDialog.js';
import { NewReportDialog } from '../src/components/modals/NewReportDialog.js';
import { NoteCaptureDialog } from '../src/components/modals/NoteCaptureDialog.js';
import { SpawnRogueDialog, deriveWebSpawnContext } from '../src/components/modals/SpawnRogueDialog.js';
import { noteCaptureStore } from '@murder/ui-core/store/notes/noteCaptureStore.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(() => {
  cleanup();
  noteCaptureStore.getState().reset();
});

describe('NewTicketDialog', () => {
  it('submits title via ticket.quick_create when builtin ticket is unavailable', async () => {
    const { bus } = makeStore();
    bus.stubCommand('ticket.quick_create', {
      handled: true,
      ticket_id: 't-001',
      title: 'fix the bug',
    });
    const onClose = vi.fn();
    renderWithStore(<NewTicketDialog open onClose={onClose} />, { bus });

    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.queryByLabelText('Instructions')).toBeNull();
    const input = screen.getByLabelText('Title');
    fireEvent.change(input, { target: { value: 'fix the bug' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(bus.commandCalls).toEqual([
      { name: 'ticket.quick_create', params: { title: 'fix the bug' } },
    ]);
  });

  it('submits title + instructions via workflow.start when builtin ticket is available', async () => {
    const { store, bus } = makeStore();
    seedSlice(store, 'settings', {
      ...store.getState().settings,
      startupRogue: { harness: 'claude_code', model: 'sonnet', effort: null },
    });
    bus.stubCommand('workflow.start', {
      workflow_id: 'wf-1',
      run_ticket_id: 't-run',
      stage_ticket_ids: { work: 't-002' },
    });
    const onClose = vi.fn();
    renderWithStore(<NewTicketDialog open onClose={onClose} />, { store, bus });

    expect(screen.getByLabelText('Instructions')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'ship it' } });
    fireEvent.change(screen.getByLabelText('Instructions'), {
      target: { value: 'do carefully' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Start' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(bus.commandCalls).toEqual([
      {
        name: 'workflow.start',
        params: { name: 'ticket', args: { title: 'ship it', prompt: 'do carefully' } },
      },
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
  function stubSpawnBus(
    bus: ReturnType<typeof makeStore>['bus'],
    favorites: { name: string; harness: string; model: string; effort: string }[] = [],
  ): void {
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
    bus.stubQuery('spawn_favorites.get', { favorites });
    bus.stubCommand('crow.spawn_rogue', {
      handled: true,
      agent_id: 'rogue-1',
    });
    bus.stubCommand('agent.message', { ok: true });
    bus.stubCommand('spawn_favorites.set', (p) => ({
      ok: true,
      favorites: (p as { favorites: typeof favorites }).favorites,
    }));
  }

  it('submits spawn via crow.spawn_rogue after loading options', async () => {
    const { bus } = makeStore();
    stubSpawnBus(bus);
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

  it('applies a favorite from the dropdown', async () => {
    const { bus } = makeStore();
    stubSpawnBus(bus, [
      { name: 'OpusMed', harness: 'claude_code', model: 'opus', effort: 'high' },
    ]);
    renderWithStore(<SpawnRogueDialog open onClose={vi.fn()} />, { bus });

    await waitFor(() => expect(screen.getByLabelText('Favorite')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Favorite'), { target: { value: '0' } });
    await waitFor(() => {
      expect((screen.getByLabelText('Model') as HTMLSelectElement).value).toBe('opus');
    });
    expect((screen.getByLabelText('Effort') as HTMLSelectElement).value).toBe('high');
  });

  it('creates a favorite via spawn_favorites.set', async () => {
    const { bus } = makeStore();
    stubSpawnBus(bus);
    renderWithStore(<SpawnRogueDialog open onClose={vi.fn()} />, { bus });

    await waitFor(() => expect(screen.getByLabelText('Save current as favorite')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Save current as favorite'), {
      target: { value: 'SonnetLite' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save favorite' }));

    await waitFor(() => {
      expect(bus.commandCalls.some((c) => c.name === 'spawn_favorites.set')).toBe(true);
    });
    expect(bus.commandCalls.find((c) => c.name === 'spawn_favorites.set')?.params).toMatchObject({
      favorites: [{ name: 'SonnetLite', harness: 'claude_code', model: 'sonnet', effort: 'medium' }],
    });
  });

  it('renames the selected favorite', async () => {
    const { bus } = makeStore();
    stubSpawnBus(bus, [
      { name: 'OpusMed', harness: 'claude_code', model: 'opus', effort: 'high' },
    ]);
    renderWithStore(<SpawnRogueDialog open onClose={vi.fn()} />, { bus });

    await waitFor(() => expect(screen.getByLabelText('Favorite')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Favorite'), { target: { value: '0' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Rename' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Rename' }));
    fireEvent.change(screen.getByLabelText('Rename favorite'), { target: { value: 'OpusMax' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save name' }));

    await waitFor(() => {
      expect(bus.commandCalls.some((c) => c.name === 'spawn_favorites.set')).toBe(true);
    });
    expect(bus.commandCalls.find((c) => c.name === 'spawn_favorites.set')?.params).toMatchObject({
      favorites: [{ name: 'OpusMax', harness: 'claude_code', model: 'opus', effort: 'high' }],
    });
  });

  it('deletes the selected favorite after confirm', async () => {
    const { bus } = makeStore();
    stubSpawnBus(bus, [
      { name: 'OpusMed', harness: 'claude_code', model: 'opus', effort: 'high' },
    ]);
    renderWithStore(<SpawnRogueDialog open onClose={vi.fn()} />, { bus });

    await waitFor(() => expect(screen.getByLabelText('Favorite')).toBeTruthy());
    fireEvent.change(screen.getByLabelText('Favorite'), { target: { value: '0' } });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Delete' })).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(screen.getByText(/Delete “OpusMed”/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => {
      expect(bus.commandCalls.some((c) => c.name === 'spawn_favorites.set')).toBe(true);
    });
    expect(bus.commandCalls.find((c) => c.name === 'spawn_favorites.set')?.params).toMatchObject({
      favorites: [],
    });
  });

  it('includes doc kickoff when docView is open and the checkbox is checked', async () => {
    const { store, bus } = makeStore();
    stubSpawnBus(bus);
    seedSlice(store, 'docView', {
      open: { kind: 'plan', name: 'ship-it' },
      body: '# plan',
      status: 'ready',
      error: null,
    });
    const onClose = vi.fn();
    renderWithStore(<SpawnRogueDialog open onClose={onClose} />, { store, bus });

    await waitFor(() => expect(screen.getByLabelText(/Read “ship-it”/)).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Spawn' }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());

    await waitFor(() => {
      expect(bus.commandCalls.some((c) => c.name === 'agent.message')).toBe(true);
    });
    expect(bus.commandCalls.find((c) => c.name === 'agent.message')?.params).toMatchObject({
      message: 'Please read .murder/plans/ship-it.md before starting.',
    });
  });
});

describe('deriveWebSpawnContext', () => {
  it('builds reference-by-path from an open doc', () => {
    expect(deriveWebSpawnContext(null)).toBeNull();
    expect(deriveWebSpawnContext({ kind: 'note', name: 'scratch' })).toEqual({
      title: 'scratch',
      path: '.murder/notes/scratch.md',
    });
  });
});

describe('NewReportDialog', () => {
  it('submits name via report.create and opens the doc', async () => {
    const { store, bus } = makeStore();
    bus.stubCommand('report.create', {
      handled: true,
      ok: true,
      name: 'status-2026',
      error: null,
    });
    const openDoc = vi.fn();
    store.setState((s) => ({
      actions: {
        ...s.actions,
        docView: { ...s.actions.docView, open: openDoc },
      },
    }));
    const onClose = vi.fn();
    renderWithStore(<NewReportDialog open onClose={onClose} />, { store, bus });

    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'status-2026' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(bus.commandCalls).toEqual([
      { name: 'report.create', params: { name: 'status-2026', body: '' } },
    ]);
    expect(openDoc).toHaveBeenCalledWith('report', 'status-2026');
  });
});

describe('NoteCaptureDialog', () => {
  it('submits draft via notetaker.capture.submit and resets the capture store', async () => {
    const { bus } = makeStore();
    bus.stubCommand('notetaker.capture.submit', {
      handled: true,
      ok: true,
      name: 'auto-note',
    });
    noteCaptureStore.getState().setDraft('hello world');
    noteCaptureStore.getState().setTitle('my title');
    const onClose = vi.fn();
    renderWithStore(<NoteCaptureDialog open onClose={onClose} />, { bus });

    expect(screen.getByDisplayValue('hello world')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(bus.commandCalls).toEqual([
      {
        name: 'notetaker.capture.submit',
        params: { raw: 'hello world', title: 'my title' },
      },
    ]);
    expect(noteCaptureStore.getState().draftText).toBe('');
    expect(noteCaptureStore.getState().titleText).toBe('');
  });
});
