/**
 * DocViewer (DS reskin) renders the open doc off a seeded `docView` slice: the DS Panel with the kind
 * Tag + name in the title, and plain `<pre>` or markdown body per documentDisplayMode.
 * Also: Edit → document.editor.start, plan spawn, goto-line chord.
 */

import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { cleanup, fireEvent, screen } from '@testing-library/react';
import { act } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { DocViewer } from '../src/components/stage/DocViewer.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('DocViewer (DS reskin)', () => {
  it('renders nothing when no doc is open', () => {
    const { store } = makeStore();
    const { container } = renderWithStoreContainer(store);
    expect(container.querySelector('.mds-doc')).toBeNull();
  });

  it('renders the open doc in a DS Panel with kind Tag + body', () => {
    const { store } = makeStore();
    seedSlice(store, 'docView', {
      open: { kind: 'plan', name: 'split-orchestrator' },
      body: '# Decompose the Orchestrator',
      status: 'ready',
      error: null,
    });
    renderWithStore(<DocViewer />, { store });

    expect(document.querySelector('.mds-doc .mds-panel')).toBeTruthy();
    expect(document.querySelector('.mds-tag')?.textContent).toBe('plan');
    expect(screen.getByText('split-orchestrator')).toBeTruthy();
    expect(screen.getByText(/Decompose the Orchestrator/)).toBeTruthy();
    expect(document.querySelector('pre.mds-doc__body')).toBeTruthy();
    expect(screen.getByLabelText('close')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'plan' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Edit' })).toBeTruthy();
  });

  it('renders markdown when documentDisplayMode is markdown', () => {
    const { store } = makeStore();
    store.setState((s) => ({
      settings: { ...s.settings, documentDisplayMode: 'markdown' },
    }));
    seedSlice(store, 'docView', {
      open: { kind: 'plan', name: 'md-plan' },
      body: '# Title\n\nHello **world**',
      status: 'ready',
      error: null,
    });
    renderWithStore(<DocViewer />, { store });

    expect(document.querySelector('.mds-doc__body--md')).toBeTruthy();
    expect(document.querySelector('.mds-md__h1')?.textContent).toBe('Title');
    expect(document.querySelector('.mds-md__strong')?.textContent).toBe('world');
    expect(document.querySelector('pre.mds-doc__body')).toBeNull();
  });

  it('shows Edit + spawn-planner actions for a plan doc', () => {
    const { store } = makeStore();
    seedSlice(store, 'docView', {
      open: { kind: 'plan', name: 'alpha' },
      body: 'body',
      status: 'ready',
      error: null,
    });
    renderWithStore(<DocViewer />, { store });
    expect(screen.getByRole('button', { name: 'Edit' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'plan' })).toBeTruthy();
  });

  it('omits spawn-planner for notes', () => {
    const { store } = makeStore();
    seedSlice(store, 'docView', {
      open: { kind: 'note', name: 'scratch' },
      body: 'x',
      status: 'ready',
      error: null,
    });
    renderWithStore(<DocViewer />, { store });
    expect(screen.getByRole('button', { name: 'Edit' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'plan' })).toBeNull();
  });

  it('starts a document editor session and mounts the terminal surface', async () => {
    const bus = new FakeApplicationClient();
    const SESSION = '00000000-0000-0000-0000-000000000123';
    bus.stubCommand('document.editor.start', {
      status: 'active',
      document_path: '/repo/.murder/plans/alpha.md',
      terminal_session_id: SESSION,
      reused: false,
    });
    bus.stubCommand('document.editor.resize', { handled: true });
    bus.stubCommand('document.editor.status', {
      status: 'active',
      document_path: '/repo/.murder/plans/alpha.md',
      terminal_session_id: SESSION,
    });
    const { store } = makeStore();
    seedSlice(store, 'docView', {
      open: { kind: 'plan', name: 'alpha' },
      body: 'hello',
      status: 'ready',
      error: null,
    });
    renderWithStore(<DocViewer />, { store, bus });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
      await Promise.resolve();
      await Promise.resolve();
    });
    await flush();

    expect(bus.commandCalls.some((c) => c.name === 'document.editor.start')).toBe(true);
    expect(bus.commandCalls.find((c) => c.name === 'document.editor.start')?.params).toMatchObject({
      kind: 'plan',
      name: 'alpha',
    });
    act(() => {
      bus.emitTerminal(SESSION, 'editor frame');
    });
    expect(document.querySelector('.mds-tmux__frame')?.textContent).toContain('editor frame');
  });

  it('spawns a planner from the plan action', async () => {
    const { store, bus } = makeStore();
    const spawn = vi.spyOn(store.getState().actions.plans, 'spawnPlanner').mockResolvedValue();
    seedSlice(store, 'docView', {
      open: { kind: 'plan', name: 'beta' },
      body: 'x',
      status: 'ready',
      error: null,
    });
    renderWithStore(<DocViewer />, { store, bus });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'plan' }));
    });
    expect(spawn).toHaveBeenCalledWith('beta');
  });

  it('shows goto overlay while capturing g+digits', () => {
    const { store } = makeStore();
    seedSlice(store, 'docView', {
      open: { kind: 'note', name: 'n' },
      body: 'a\nb\nc\nd',
      status: 'ready',
      error: null,
    });
    renderWithStore(<DocViewer />, { store });
    act(() => {
      fireEvent.keyDown(window, { key: 'g' });
    });
    expect(document.querySelector('.mds-goto')).toBeTruthy();
    act(() => {
      fireEvent.keyDown(window, { key: '2' });
    });
    expect(document.querySelector('.mds-goto__digits')?.textContent).toBe('2');
  });
});

function renderWithStoreContainer(store: ReturnType<typeof makeStore>['store']): {
  container: HTMLElement;
} {
  renderWithStore(<DocViewer />, { store });
  return { container: document.body };
}
