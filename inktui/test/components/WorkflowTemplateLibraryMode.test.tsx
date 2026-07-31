/**
 * Workflow template library — its registry/action boundary is deliberately store independent.
 * These tests exercise the mode through the normal captured-input path so keyboard filtering and
 * built-in protections do not depend on application wiring.
 */

import { describe, expect, it, vi } from 'vitest';
import {
  copiedWorkflowName,
  copyWorkflowTemplate,
  filterWorkflowTemplates,
  partitionWorkflowTemplates,
  WORKFLOW_TEMPLATE_LIBRARY_MODE_ID,
  workflowTemplateLibraryMode,
} from '../../src/components/WorkflowTemplateLibraryMode.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import type { WorkflowTemplate } from '../../src/store/workflows/workflowsSlice.js';
import { makeKey } from '../input/key.js';

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
        instructions: 'Plan it',
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

function actions() {
  return {
    run: vi.fn(),
    newWorkflowTemplate: vi.fn(),
    copy: vi.fn(),
    edit: vi.fn(),
    delete: vi.fn(),
  };
}

describe('WorkflowTemplateLibraryMode helpers', () => {
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
    const original = workflow('Release Plan', {
      builtin: true,
      stages: [
        {
          id: 'plan',
          title: 'Plan',
          instructions: 'Original instructions',
          harness: 'codex',
          model: 'o3',
          depends_on: [],
          gate: 'auto',
        },
      ],
    });
    const names = new Set(['Release Plan', 'Copy of Release Plan']);

    expect(copiedWorkflowName('Release Plan', names)).toBe('Copy of Release Plan 2');
    const copy = copyWorkflowTemplate(original, names);
    expect(copy).toMatchObject({
      name: 'Copy of Release Plan 2',
      builtin: false,
      definition_version: 1,
    });
    expect(copy.stages).not.toBe(original.stages);
    expect(copy.stages?.[0]).not.toBe(original.stages?.[0]);
    expect(original).toMatchObject({ name: 'Release Plan', builtin: true, definition_version: 3 });
  });
});

describe('WorkflowTemplateLibraryMode', () => {
  it('starts the exact focused template from Enter', () => {
    const stores = createInputStores(['notes'], 'notes');
    const handle = actions();
    const first = workflow('Alpha');
    const focused = workflow('Release Plan', { mode: 'generative' });
    const builtIn = workflow('Starter', { builtin: true });
    const mode = workflowTemplateLibraryMode(stores.modes, {
      workflows: [builtIn, focused, first],
      focusedName: 'Release Plan',
      actions: handle,
    });
    stores.modes.getState().enter(mode);

    expect(selectActiveMode(stores.modes)?.id).toBe(WORKFLOW_TEMPLATE_LIBRARY_MODE_ID);
    mode.onIntent('enter');
    expect(handle.run).toHaveBeenCalledWith(focused);
  });

  it('filters names, creates a blank draft, and dismisses', () => {
    const stores = createInputStores(['notes'], 'notes');
    const handle = actions();
    const alpha = workflow('Alpha');
    const release = workflow('Release Plan');
    const mode = workflowTemplateLibraryMode(stores.modes, {
      workflows: [alpha, release],
      actions: handle,
    });
    stores.modes.getState().enter(mode);

    mode.onIntent('filter');
    for (const character of 'release') {
      expect(mode.onUncaptured?.(character, makeKey())).toBe(true);
    }
    mode.onIntent('enter'); // finish filtering
    mode.onIntent('enter'); // run selected match
    expect(handle.run).toHaveBeenCalledWith(release);

    mode.onIntent('new');
    expect(handle.newWorkflowTemplate).toHaveBeenCalledOnce();
    mode.onIntent('escape');
    expect(selectActiveMode(stores.modes)).toBeNull();
  });

  it('copies built-ins but rejects their edit/delete mutations', () => {
    const stores = createInputStores(['notes'], 'notes');
    const handle = actions();
    const builtIn = workflow('Starter', { builtin: true });
    const mode = workflowTemplateLibraryMode(stores.modes, {
      workflows: [builtIn],
      actions: handle,
    });
    stores.modes.getState().enter(mode);

    mode.onIntent('copy');
    expect(handle.copy).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'Copy of Starter',
        builtin: false,
        definition_version: 1,
      }),
    );
    mode.onIntent('edit');
    expect(handle.edit).not.toHaveBeenCalled();
    mode.onIntent('delete');
    expect(handle.delete).not.toHaveBeenCalled();
  });

  it('requires explicit confirmation before deleting an owned template', () => {
    const stores = createInputStores(['notes'], 'notes');
    const handle = actions();
    const owned = workflow('Release Plan');
    const mode = workflowTemplateLibraryMode(stores.modes, { workflows: [owned], actions: handle });
    stores.modes.getState().enter(mode);

    mode.onIntent('delete');
    expect(handle.delete).not.toHaveBeenCalled();
    mode.onUncaptured?.('n', makeKey());
    expect(handle.delete).not.toHaveBeenCalled();
    mode.onIntent('delete');
    mode.onUncaptured?.('y', makeKey());
    expect(handle.delete).toHaveBeenCalledWith(owned);
  });
});
