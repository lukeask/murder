/**
 * WorkflowTemplateEditor `/` stage search — TUI parity filter/jump overlay.
 * Mocks @xyflow/react so jsdom does not hit RF ResizeObserver / store loops.
 */

import type { WorkflowTemplate } from '@murder/ui-core/store/workflows/workflowsSlice.js';
import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

vi.mock('@xyflow/react', () => {
  const React = require('react') as typeof import('react');
  return {
    ReactFlowProvider: ({ children }: { children: React.ReactNode }) =>
      React.createElement(React.Fragment, null, children),
    ReactFlow: () => React.createElement('div', { 'data-testid': 'rf-stub' }),
    Background: () => null,
    BackgroundVariant: { Dots: 'dots' },
    Controls: () => null,
    MiniMap: () => null,
    SelectionMode: { Partial: 'partial' },
    useEdgesState: () => [[], vi.fn(), vi.fn()],
    useNodesState: () => [[], vi.fn(), vi.fn()],
    useReactFlow: () => ({
      fitView: vi.fn(),
      getNode: vi.fn(),
    }),
  };
});

import { WorkflowTemplateEditor } from '../src/components/modes/WorkflowTemplateEditor.js';

afterEach(cleanup);

function template(): WorkflowTemplate {
  return {
    name: 'deploy',
    description: 'deploy flow',
    mode: 'static',
    definition_version: 1,
    stages: [
      {
        id: 'plan',
        title: 'Plan work',
        instructions: 'plan',
        harness: 'codex',
        model: 'o3',
        depends_on: [],
        gate: 'auto',
      },
      {
        id: 'ship',
        title: 'Ship it',
        instructions: 'ship',
        harness: 'codex',
        model: 'o3',
        depends_on: ['plan'],
        gate: 'auto',
      },
    ],
    inputs: {},
  };
}

describe('WorkflowTemplateEditor stage search', () => {
  it('opens on / and jumps to a matching stage on Enter', async () => {
    const { store, bus } = makeStore();
    const wf = template();
    bus.stubQuery('workflows.get', {
      ok: true,
      workflows: [wf],
      revision: 'rev-1',
    });
    seedSlice(store, 'workflows', {
      items: [wf],
      status: 'ready',
      error: null,
      revision: 'rev-1',
    });

    renderWithStore(
      <WorkflowTemplateEditor templateName="deploy" onClose={() => {}} />,
      { store, bus },
    );

    await waitFor(() => {
      expect(screen.getByRole('dialog', { name: 'Workflow template editor' })).toBeTruthy();
      expect(screen.queryByText('Loading template…')).toBeNull();
    });

    fireEvent.keyDown(document, { key: '/' });
    const input = await screen.findByRole('searchbox');
    expect(input).toBeTruthy();

    fireEvent.change(input, { target: { value: 'ship' } });
    expect(screen.getByText('1 match')).toBeTruthy();
    expect(screen.getByRole('button', { name: /ship/i })).toBeTruthy();

    fireEvent.keyDown(input, { key: 'Enter' });
    await waitFor(() => {
      expect(screen.queryByRole('searchbox')).toBeNull();
    });
  });

  it('dismisses search with Escape', async () => {
    const { store, bus } = makeStore();
    const wf = template();
    bus.stubQuery('workflows.get', {
      ok: true,
      workflows: [wf],
      revision: 'rev-1',
    });
    seedSlice(store, 'workflows', {
      items: [wf],
      status: 'ready',
      error: null,
      revision: 'rev-1',
    });

    renderWithStore(
      <WorkflowTemplateEditor templateName="deploy" onClose={() => {}} />,
      { store, bus },
    );

    await waitFor(() => expect(screen.queryByText('Loading template…')).toBeNull());

    fireEvent.keyDown(document, { key: '/' });
    const input = await screen.findByRole('searchbox');
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByRole('searchbox')).toBeNull();
  });
});
