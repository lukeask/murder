/**
 * PromptTemplateManager — open/list smoke + settings entry opens via creation dialogs.
 */

import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { PromptTemplateManager } from '../src/components/modes/PromptTemplateManager.js';
import { SettingsPanel } from '../src/components/panels/SettingsPanel.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

describe('PromptTemplateManager', () => {
  it('lists seeded templates and shows expansion preview', async () => {
    const { store, bus } = makeStore();
    bus.stubQuery('templates.get', {
      ok: true,
      templates: [
        { name: 'greet', body: 'Hello {who}' },
        { name: 'wrap', body: 'See :greet: again' },
      ],
    });
    bus.stubQuery('workflows.get', { ok: true, workflows: [], revision: '0' });
    seedSlice(store, 'templates', {
      items: [
        { name: 'greet', body: 'Hello {who}' },
        { name: 'wrap', body: 'See :greet: again' },
      ],
      status: 'ready',
      error: null,
    });
    seedSlice(store, 'workflows', {
      items: [],
      status: 'ready',
      error: null,
      revision: '0',
    });

    renderWithStore(<PromptTemplateManager onClose={() => {}} />, { store, bus });

    const list = screen.getByRole('list', { name: /Prompt templates/i });
    expect(list.textContent).toContain(':greet:');
    expect(list.textContent).toContain(':wrap:');

    fireEvent.click(screen.getByRole('listitem', { name: /:wrap:/ }));
    await waitFor(() => {
      expect(screen.getByText('Expands')).toBeTruthy();
    });
    expect(screen.getByText(/Hello \{who\}/)).toBeTruthy();
  });

  it('loads templates when opened', async () => {
    const { store, bus } = makeStore();
    const load = vi.fn(async () => {
      store.setState({
        templates: {
          items: [{ name: 'loaded', body: 'body' }],
          status: 'ready',
          error: null,
        },
      });
    });
    store.setState({
      actions: {
        ...store.getState().actions,
        templates: { ...store.getState().actions.templates, load },
      },
    });
    seedSlice(store, 'workflows', {
      items: [],
      status: 'ready',
      error: null,
      revision: '0',
    });

    renderWithStore(<PromptTemplateManager onClose={() => {}} />, { store, bus });

    await waitFor(() => {
      expect(load).toHaveBeenCalled();
    });
    expect(screen.getByRole('listitem', { name: /:loaded:/ })).toBeTruthy();
  });

  it('settings “Open Prompt Templates…” invokes openPromptTemplates', () => {
    const openPromptTemplates = vi.fn();
    const { store, bus } = makeStore();
    bus.stubQuery('themes.get', { ok: true, themes: [] });
    renderWithStore(<SettingsPanel />, {
      store,
      bus,
      creationDialogs: {
        openSpawn: () => {},
        openTicket: () => {},
        openPlan: () => {},
        openReport: () => {},
        openNoteCapture: () => {},
        openPromptTemplates,
        openHelp: () => {},
        openWorkflowLibrary: () => {},
        openWorkflowLaunch: () => {},
        openWorkflowEditor: () => {},
      },
    });

    fireEvent.click(screen.getByRole('button', { name: /Open Prompt Templates/i }));
    expect(openPromptTemplates).toHaveBeenCalledOnce();
  });
});
