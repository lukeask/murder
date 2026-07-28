/**
 * PromptTemplateManagerMode tests — CRUD + referential warnings extracted from SettingsModal.
 */

import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it, vi } from 'vitest';
import {
  PROMPT_TEMPLATE_MANAGER_MODE_ID,
  promptTemplateManagerMode,
} from '../../src/components/PromptTemplateManagerMode.js';
import { Overlay } from '../../src/components/Overlay.js';
import {
  collectBodyPlaceholders,
  collectUnknownInlineRefs,
  findWorkflowReferences,
  formatWorkflowTemplateRef,
  validateTemplateName,
} from '../../src/components/promptTemplates/refs.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { matchKeymap } from '../../src/input/keymap.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import type { WorkflowTemplate } from '../../src/store/workflows/workflowsSlice.js';
import { makeKey } from '../input/key.js';

const ESC = '\x1b';

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

function RootInput(): null {
  useRootInput();
  return null;
}

function Harness({
  stores,
}: {
  readonly stores: ReturnType<typeof createInputStores>;
}): JSX.Element {
  return (
    <InputStoresProvider value={stores}>
      <RootInput />
      <Overlay />
    </InputStoresProvider>
  );
}

function fakeActions() {
  const removed: string[] = [];
  const renamed: Array<[string, string]> = [];
  const saved: Array<[string, string]> = [];
  return {
    removed,
    renamed,
    saved,
    handle: {
      remove: (name: string) => removed.push(name),
      rename: (oldName: string, newName: string) => renamed.push([oldName, newName]),
      save: (name: string, body: string) => saved.push([name, body]),
    },
  };
}

const sampleWorkflow = {
  name: 'review',
  description: '',
  mode: 'static',
  stages: [
    {
      id: 's1',
      title: 'Review',
      instructions: 'Use :greet: then continue',
      harness: 'claude_code',
      model: 'default',
      depends_on: [],
    },
  ],
} as unknown as WorkflowTemplate;

const multiRefWorkflows = [
  sampleWorkflow,
  {
    name: 'ship',
    description: '',
    mode: 'static',
    stages: [
      {
        id: 'plan',
        title: 'Plan with :greet:',
        instructions: 'Also :greet: here',
        harness: 'claude_code',
        model: 'default',
        depends_on: [],
      },
      {
        id: 'do',
        title: 'Do',
        instructions: 'Ignore :other:',
        harness: 'claude_code',
        model: 'default',
        depends_on: ['plan'],
      },
    ],
  },
] as unknown as WorkflowTemplate[];

describe('promptTemplates/refs helpers', () => {
  it('collects placeholders and unknown inline refs', () => {
    expect(collectBodyPlaceholders('hi {a} and {b} and {a}')).toEqual(['a', 'b']);
    expect(collectUnknownInlineRefs('see :greet: and :missing:', new Set(['greet']))).toEqual([
      'missing',
    ]);
  });

  it('finds workflow references and validates names', () => {
    expect(findWorkflowReferences('greet', [sampleWorkflow])).toEqual([
      { workflowName: 'review', stageId: 's1', field: 'instructions' },
    ]);
    expect(formatWorkflowTemplateRef({ workflowName: 'review', stageId: 's1', field: 'title' })).toBe(
      'review/s1.title',
    );
    expect(validateTemplateName('ok', null, [])).toBeNull();
    expect(validateTemplateName('review-context', null, [])).toBeNull();
    expect(validateTemplateName('bad!', null, [])).toContain('invalid');
    expect(validateTemplateName('100', null, [])).toContain('invalid');
    expect(validateTemplateName('x', null, [{ name: 'x' }])).toContain('already exists');
  });
});

describe('PromptTemplateManagerMode', () => {
  it('opens, lists templates, Esc dismisses', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { handle } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(
      promptTemplateManagerMode(stores.modes, null, {
        templates: [
          { name: 'greet', body: 'hello {name}' },
          { name: 'bye', body: 'goodbye' },
        ],
        templateActions: handle,
        workflows: [],
      }),
    );
    await tick();
    expect(selectActiveMode(stores.modes)?.id).toBe(PROMPT_TEMPLATE_MANAGER_MODE_ID);
    const frame = lastFrame() ?? '';
    expect(frame).toContain('Prompt Templates');
    expect(frame).toContain(':greet');
    expect(frame).toContain(':bye');
    stdin.write('j');
    await tick();
    expect(lastFrame()).toContain('inputs: {name}');

    stdin.write(ESC);
    await tick();
    expect(selectActiveMode(stores.modes)).toBeNull();
  });

  it('creates a template with name then multiline body', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { handle, saved } = fakeActions();
    const { stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(
      promptTemplateManagerMode(stores.modes, null, {
        templates: [],
        templateActions: handle,
      }),
    );
    await tick();
    stdin.write('\r'); // begin create on + New
    await tick();
    stdin.write('n');
    stdin.write('e');
    stdin.write('w');
    await tick();
    stdin.write('\r'); // name → body
    await tick();
    stdin.write('b');
    stdin.write('o');
    stdin.write('d');
    stdin.write('y');
    await tick();
    stdin.write('\r'); // save
    await tick();
    expect(saved).toContainEqual(['new', 'body']);
  });

  it('Shift+Enter in body inserts newline; keymap lists it before bare Enter', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { handle, saved } = fakeActions();
    const mode = promptTemplateManagerMode(stores.modes, null, {
      templates: [],
      templateActions: handle,
    });
    // Regression: bare `{ return }` before `{ shift, return }` would steal Shift+Enter as save.
    expect(matchKeymap(mode.keymap, '', makeKey({ return: true, shift: true }))).toBe('newline');
    expect(matchKeymap(mode.keymap, '', makeKey({ return: true }))).toBe('enter');

    const { stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(mode);
    await tick();
    stdin.write('\r'); // begin create on + New
    await tick();
    for (const ch of 'multi') stdin.write(ch);
    await tick();
    stdin.write('\r'); // name → body
    await tick();
    stdin.write('a');
    await tick();
    mode.onIntent('newline');
    await tick();
    stdin.write('b');
    await tick();
    stdin.write('\r'); // save
    await tick();
    expect(saved).toContainEqual(['multi', 'a\nb']);
  });

  it('renames via r and rejects collisions', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { handle, renamed } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(
      promptTemplateManagerMode(stores.modes, null, {
        templates: [
          { name: 'aaa', body: 'x' },
          { name: 'bbb', body: 'y' },
        ],
        templateActions: handle,
      }),
    );
    await tick();
    stdin.write('j'); // :aaa
    await tick();
    stdin.write('r');
    await tick();
    stdin.write('\x7f');
    stdin.write('\x7f');
    stdin.write('\x7f');
    await tick();
    stdin.write('b');
    stdin.write('b');
    stdin.write('b');
    await tick();
    stdin.write('\r');
    await tick();
    expect(lastFrame()).toContain('already exists');
    expect(renamed).toHaveLength(0);
  });

  it('rename with workflow refs lists every affected site and confirms before renaming', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { handle, renamed } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(
      promptTemplateManagerMode(stores.modes, null, {
        templates: [{ name: 'greet', body: 'hi' }],
        templateActions: handle,
        workflows: multiRefWorkflows,
      }),
    );
    await tick();
    stdin.write('j');
    await tick();
    const preview = lastFrame() ?? '';
    expect(preview).toContain('review/s1.instructions');
    expect(preview).toContain('ship/plan.title');
    expect(preview).toContain('ship/plan.instructions');
    expect(preview).not.toContain('…');

    stdin.write('r');
    await tick();
    stdin.write('\x7f');
    stdin.write('\x7f');
    stdin.write('\x7f');
    stdin.write('\x7f');
    stdin.write('\x7f');
    await tick();
    stdin.write('h');
    stdin.write('i');
    await tick();
    stdin.write('\r');
    await tick();

    const confirm = lastFrame() ?? '';
    expect(confirm).toContain('will keep :greet:');
    expect(confirm).toContain('workflow refs that will keep :greet:');
    expect(confirm).toContain('review/s1.instructions');
    expect(confirm).toContain('ship/plan.title');
    expect(confirm).toContain('ship/plan.instructions');
    expect(renamed).toHaveLength(0);

    stdin.write('y');
    await tick();
    expect(renamed).toContainEqual(['greet', 'hi']);
    expect(lastFrame()).toContain('still use :greet:');
  });

  it('delete confirms and removes; warns when workflows reference it', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { handle, removed } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(
      promptTemplateManagerMode(stores.modes, null, {
        templates: [{ name: 'greet', body: 'hi' }],
        templateActions: handle,
        workflows: [sampleWorkflow],
      }),
    );
    await tick();
    stdin.write('j');
    await tick();
    expect(lastFrame()).toContain('used by (1):');
    expect(lastFrame()).toContain('review/s1.instructions');
    stdin.write('d');
    await tick();
    expect(lastFrame()).toContain('referenced by');
    expect(lastFrame()).toContain('review/s1.instructions');
    stdin.write('y');
    await tick();
    expect(removed).toContainEqual('greet');
  });

  it('shows workflow name collision marker', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const { handle } = fakeActions();
    const { lastFrame, stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(
      promptTemplateManagerMode(stores.modes, null, {
        templates: [{ name: 'review', body: 'x' }],
        templateActions: handle,
        workflows: [sampleWorkflow],
      }),
    );
    await tick();
    stdin.write('j');
    await tick();
    expect(lastFrame()).toContain('workflow name');
  });

  it('onDismiss fires when closed', async () => {
    const stores = createInputStores(['notes'], 'notes');
    const onDismiss = vi.fn();
    const { stdin } = render(<Harness stores={stores} />);
    stores.modes.getState().enter(
      promptTemplateManagerMode(stores.modes, null, {
        templates: [],
        templateActions: fakeActions().handle,
        onDismiss,
      }),
    );
    await tick();
    stdin.write(ESC);
    await tick();
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
