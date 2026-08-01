/**
 * Workflow template library + launch review — list/filter/run happy paths on FakeApplicationClient.
 */

import type { WorkflowTemplate } from '@murder/ui-core/store/workflows/workflowsSlice.js';
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WorkflowTemplateLibrary } from '../src/components/modes/WorkflowTemplateLibrary.js';
import { WorkflowLaunchReview } from '../src/components/modes/WorkflowLaunchReview.js';
import {
  copiedWorkflowName,
  copyWorkflowTemplate,
  filterWorkflowTemplates,
  partitionWorkflowTemplates,
} from '../src/workflowTemplates/libraryHelpers.js';
import { makeStore, renderWithStore, seedSlice } from './helpers.js';

afterEach(cleanup);

function workflow(name: string, overrides: Partial<WorkflowTemplate> = {}): WorkflowTemplate {
  return {
    name,
    description: `${name} description`,
    mode: 'static',
    definition_version: 3,
    stages: [
      {
        id: 'plan',
        title: 'Plan',
        instructions: 'Plan {topic}',
        harness: 'codex',
        model: 'o3',
        depends_on: [],
        gate: 'auto',
      },
    ],
    inputs: { topic: { label: 'Topic', required: true } },
    ...overrides,
  };
}

function libraryList(): HTMLElement {
  return screen.getByRole('listbox', { name: 'Workflow templates' });
}

describe('workflow library helpers', () => {
  it('partitions and filters a name-sorted registry', () => {
    const alpha = workflow('Alpha');
    const builtIn = workflow('Built in', { builtin: true });
    const zebra = workflow('zebra');

    expect(partitionWorkflowTemplates([zebra, builtIn, alpha])).toEqual({
      mine: [alpha, zebra],
      builtIn: [builtIn],
    });
    expect(filterWorkflowTemplates([alpha, builtIn, zebra], ' BUILT ')).toEqual([builtIn]);
  });

  it('makes a collision-free detached draft when copying', () => {
    const original = workflow('Release Plan', { builtin: true });
    const names = new Set(['Release Plan', 'Copy of Release Plan']);
    expect(copiedWorkflowName('Release Plan', names)).toBe('Copy of Release Plan 2');
    const copy = copyWorkflowTemplate(original, names);
    expect(copy).toMatchObject({
      name: 'Copy of Release Plan 2',
      builtin: false,
      definition_version: 1,
    });
    expect(copy.stages).not.toBe(original.stages);
  });
});

describe('WorkflowTemplateLibrary', () => {
  it('lists mine vs built-in and runs the selected template', async () => {
    const { store, bus } = makeStore();
    const mine = workflow('deploy');
    const builtIn = workflow('ticket', { builtin: true });
    bus.stubQuery('workflows.get', {
      ok: true,
      workflows: [builtIn, mine],
      revision: 'rev-1',
    });
    seedSlice(store, 'workflows', {
      items: [builtIn, mine],
      status: 'ready',
      error: null,
      revision: 'rev-1',
    });

    const onRun = vi.fn();
    const onEdit = vi.fn();
    const onClose = vi.fn();
    renderWithStore(
      <WorkflowTemplateLibrary open onClose={onClose} onRun={onRun} onEdit={onEdit} />,
      { store, bus },
    );

    expect(screen.getByRole('dialog')).toBeTruthy();
    expect(screen.getByText('My workflow templates')).toBeTruthy();
    expect(screen.getByText('Built-in workflow templates')).toBeTruthy();
    const list = libraryList();
    await waitFor(() => expect(within(list).getByText('deploy')).toBeTruthy());
    expect(within(list).getByText('ticket')).toBeTruthy();

    fireEvent.click(within(list).getByText('deploy'));
    const footer = screen.getByRole('dialog').querySelector('.mds-dialog__foot');
    expect(footer).toBeTruthy();
    fireEvent.click(within(footer as HTMLElement).getByRole('button', { name: 'Run' }));
    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({ name: 'deploy' }));
  });

  it('loads the registry on open and focuses a named template', async () => {
    const { store, bus } = makeStore();
    const alpha = workflow('Alpha');
    const release = workflow('Release Plan');
    bus.stubQuery('workflows.get', {
      ok: true,
      workflows: [alpha, release],
      revision: 'r2',
    });

    const onRun = vi.fn();
    renderWithStore(
      <WorkflowTemplateLibrary
        open
        focusedName="Release Plan"
        onClose={() => {}}
        onRun={onRun}
        onEdit={() => {}}
      />,
      { store, bus },
    );

    await waitFor(() => expect(store.getState().workflows.items.length).toBe(2));
    await waitFor(() => expect(within(libraryList()).getByText('Release Plan')).toBeTruthy());
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Release Plan' })).toBeTruthy());
    const footer = screen.getByRole('dialog').querySelector('.mds-dialog__foot');
    fireEvent.click(within(footer as HTMLElement).getByRole('button', { name: 'Run' }));
    expect(onRun).toHaveBeenCalledWith(expect.objectContaining({ name: 'Release Plan' }));
  });

  it('routes New to onEdit blank and Copy to a detached draft', async () => {
    const { store, bus } = makeStore();
    const starter = workflow('Starter', { builtin: true });
    bus.stubQuery('workflows.get', {
      ok: true,
      workflows: [starter],
      revision: 'r3',
    });
    seedSlice(store, 'workflows', {
      items: [starter],
      status: 'ready',
      error: null,
      revision: 'r3',
    });

    const onEdit = vi.fn();
    renderWithStore(
      <WorkflowTemplateLibrary open onClose={() => {}} onRun={() => {}} onEdit={onEdit} />,
      { store, bus },
    );

    await waitFor(() => expect(within(libraryList()).getByText('Starter')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'New' }));
    expect(onEdit).toHaveBeenCalledWith({ kind: 'blank' });

    onEdit.mockClear();
    fireEvent.click(within(libraryList()).getByText('Starter'));
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    expect(onEdit).toHaveBeenCalledWith({
      kind: 'draft',
      workflow: expect.objectContaining({
        name: 'Copy of Starter',
        builtin: false,
        definition_version: 1,
      }),
    });
  });
});

describe('WorkflowLaunchReview', () => {
  it('compiles then launches with required input values', async () => {
    const { store, bus } = makeStore();
    const wf = workflow('deploy');
    seedSlice(store, 'workflows', {
      items: [wf],
      status: 'ready',
      error: null,
      revision: 'r1',
    });
    bus.stubQuery('workflow.compile', {
      ok: true,
      expanded_template: { name: 'deploy', stages: wf.stages ?? [] },
      inputs: [
        {
          name: 'topic',
          label: 'Topic',
          kind: 'text',
          required: true,
          default: null,
          inferred: false,
        },
      ],
      issues: [],
    });
    bus.stubCommand('workflow.start', {
      ok: true,
      workflow_id: 'wf-run-1',
      run_ticket_id: 'T-run',
      stage_ticket_ids: { plan: 'T-plan' },
      created_ticket_ids: ['T-run', 'T-plan'],
    });

    const onClose = vi.fn();
    const onLaunched = vi.fn();
    renderWithStore(
      <WorkflowLaunchReview open workflow={wf} onClose={onClose} onLaunched={onLaunched} />,
      { store, bus },
    );

    expect(screen.getByRole('dialog')).toBeTruthy();
    await waitFor(() => expect(screen.getByLabelText(/Topic/)).toBeTruthy());
    expect(bus.queryCalls.some((c) => c.name === 'workflow.compile')).toBe(true);

    fireEvent.change(screen.getByLabelText(/Topic/), { target: { value: 'ship it' } });
    fireEvent.click(screen.getByRole('button', { name: 'Launch' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(onLaunched).toHaveBeenCalledWith(expect.objectContaining({ name: 'deploy' }));
    expect(bus.commandCalls).toEqual([
      { name: 'workflow.start', params: { name: 'deploy', args: { topic: 'ship it' } } },
    ]);
  });

  it('blocks launch when compile fails', async () => {
    const { store, bus } = makeStore();
    const wf = workflow('broken');
    bus.stubQuery('workflow.compile', {
      ok: false,
      expanded_template: { name: 'broken', stages: [] },
      inputs: [],
      issues: [
        {
          code: 'unknown_prompt_template',
          severity: 'error',
          message: 'Unknown prompt template :missing:',
        },
      ],
    });

    renderWithStore(<WorkflowLaunchReview open workflow={wf} onClose={() => {}} />, {
      store,
      bus,
    });

    await waitFor(() =>
      expect(screen.getByText(/Unknown prompt template :missing:/)).toBeTruthy(),
    );
    expect((screen.getByRole('button', { name: 'Launch' }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});
