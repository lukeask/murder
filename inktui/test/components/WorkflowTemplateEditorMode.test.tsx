/**
 * Integration coverage for the fullscreen workflow template editor.  The graph kernels have
 * their own unit suites; these tests pin the user-visible mode shell: breakpoints,
 * transient draft state, command guards, and runtime truth rendering.
 */

import { EventEmitter } from 'node:events';
import { MouseProvider } from '@ink-tools/ink-mouse';
import { Box, render as inkRender } from 'ink';
import type { JSX } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CommandParams } from '../../src/application/ApplicationClient.js';
import { FakeApplicationClient } from '../../src/application/FakeApplicationClient.js';
import { BottomBar } from '../../src/components/BottomBar.js';
import { Overlay } from '../../src/components/Overlay.js';
import { workflowTemplateEditorMode } from '../../src/components/WorkflowTemplateEditorMode.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { createAppStore } from '../../src/store/store.js';
import type { WorkflowTemplate, WorkflowNodeTemplate } from '../../src/store/workflows/workflowsSlice.js';

const tick = async (): Promise<void> => {
  await new Promise((resolve) => setTimeout(resolve, 15));
};

const stage = (
  id: string,
  title = id,
  dependsOn: readonly string[] = [],
  overrides: Partial<WorkflowNodeTemplate> = {},
): WorkflowNodeTemplate => ({
  id,
  title,
  instructions: '',
  harness: 'codex',
  model: 'o3',
  worktree: null,
  depends_on: dependsOn,
  gate: 'auto',
  ...overrides,
});

const workflow = (overrides: Partial<WorkflowTemplate> = {}): WorkflowTemplate => ({
  name: 'release',
  description: 'Ship it',
  mode: 'static',
  stages: [
    stage('build', 'Build'),
    stage('test', 'Test', ['build']),
    stage('ship', 'Ship', ['test']),
  ],
  ...overrides,
});

interface CapturingStdout extends NodeJS.WriteStream {
  columns: number;
  rows: number;
  lastFrame(): string;
}

function stdoutFor(columns: number, rows = 30): CapturingStdout {
  const stream = new EventEmitter() as CapturingStdout;
  let frame = '';
  Object.assign(stream, {
    columns,
    rows,
    isTTY: false,
    write: (next: string) => {
      frame = next;
      return true;
    },
    lastFrame: () => frame,
  });
  return stream;
}

function Surface({
  store,
  stores,
  rows,
}: {
  readonly store: ReturnType<typeof createAppStore>['store'];
  readonly stores: ReturnType<typeof createInputStores>;
  readonly rows: number;
}): JSX.Element {
  // Mirror the App fullscreen chrome: Overlay fills the body; BottomBar keeps mode hints visible.
  return (
    <MouseProvider>
      <AppStoreProvider value={store}>
        <InputStoresProvider value={stores}>
          <Box flexDirection="column" height={rows} width="100%" overflow="hidden">
            <Box flexGrow={1} flexBasis={0} minHeight={0} overflow="hidden">
              <Overlay />
            </Box>
            <Box flexShrink={0}>
              <BottomBar />
            </Box>
          </Box>
        </InputStoresProvider>
      </AppStoreProvider>
    </MouseProvider>
  );
}

function setup(
  options: {
    readonly columns?: number;
    readonly definition?: WorkflowTemplate;
    readonly put?: unknown;
    readonly remote?: WorkflowTemplate;
  } = {},
) {
  const definition = options.definition ?? workflow();
  const fake = new FakeApplicationClient();
  const { store, dispose } = createAppStore(fake);
  const stores = createInputStores(['notes'], 'notes');
  const remote = options.remote ?? definition;
  fake.stubQuery('workflows.get', { ok: true, workflows: [remote], revision: 'remote-r2' });
  fake.stubQuery('workflow.runs.get', { ok: true, run: null, waits: [], error: null });
  fake.stubCommand(
    'workflow.put',
    options.put ??
      ((params: CommandParams<'workflow.put'>) => ({
        ok: true,
        workflow: params.workflow,
        workflows: [params.workflow],
        revision: 'r2',
      })),
  );
  fake.stubCommand('workflow.start', {
    ok: true,
    workflow_id: 'run-1',
    run_ticket_id: 'ticket-1',
    stage_ticket_ids: {},
    created_ticket_ids: [],
  });
  store.setState((state) => ({
    workflows: { ...state.workflows, items: [definition], revision: 'r1', status: 'ready' },
  }));
  const mode = workflowTemplateEditorMode(stores.modes, store, { workflow: definition });
  stores.modes.getState().enter(mode);
  const rows = 30;
  const stdout = stdoutFor(options.columns ?? 140, rows);
  const instance = inkRender(<Surface store={store} stores={stores} rows={rows} />, {
    stdout,
    stderr: stdout,
    stdin: new EventEmitter() as unknown as NodeJS.ReadStream,
    patchConsole: false,
    exitOnCtrlC: false,
    debug: true,
  });
  const close = (): void => {
    instance.unmount();
    dispose();
  };
  return { fake, store, stores, mode, stdout, close };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('WorkflowTemplateEditorMode', () => {
  it('shows graph-editor hints on the bottom bar while the fullscreen mode is open', async () => {
    const app = setup({ columns: 140 });
    await tick();
    const frame = app.stdout.lastFrame();
    expect(frame).toContain('WORKFLOWS  release');
    expect(frame).toContain('navigate graph');
    expect(frame).toContain('add stage');
    expect(frame).toContain('dependencies');
    app.close();
  });

  it('uses the wide inspector, medium overlay inspector, and narrow outline at the documented 140/90/60 widths', async () => {
    const wide = setup({ columns: 140 });
    await tick();
    expect(wide.stdout.lastFrame()).toContain('WORKFLOWS  release');
    expect(wide.stdout.lastFrame()).toContain('Inspector');
    expect(wide.stdout.lastFrame()).toContain('ID: build');
    wide.close();

    const overlay = setup({ columns: 90 });
    await tick();
    // At medium width the inspector is an in-canvas overlay rather than a side
    // column; its title and required fields must not be clipped below the viewport.
    expect(overlay.stdout.lastFrame()).toContain('Inspector');
    expect(overlay.stdout.lastFrame()).toContain('ID: build');
    expect(overlay.stdout.lastFrame()).toContain('Harness: codex');
    expect(overlay.stdout.lastFrame()).toContain('Definition order: 1 of 3');
    overlay.close();

    const narrow = setup({ columns: 60 });
    await tick();
    expect(narrow.stdout.lastFrame()).toContain('› 1. build — Build');
    expect(narrow.stdout.lastFrame()).not.toContain('Inspector');
    narrow.close();
  });

  it('labels add, remove, and cycle dependency candidates before any graph mutation', async () => {
    const add = setup({
      definition: workflow({
        stages: [stage('build'), stage('docs'), stage('test', 'Test', ['build'])],
      }),
    });
    await tick();
    add.mode.onIntent('down'); // docs, in the same root rank as build
    add.mode.onIntent('connect');
    add.mode.onIntent('up'); // build is a legal new dependency of docs
    await tick();
    expect(add.stdout.lastFrame()).toContain('ADD dependency');
    add.close();

    const remove = setup();
    await tick();
    remove.mode.onIntent('dependent'); // test
    remove.mode.onIntent('connect');
    remove.mode.onIntent('dependency'); // build is already a dependency of test
    await tick();
    expect(remove.stdout.lastFrame()).toContain('REMOVE dependency');
    remove.close();

    const cycle = setup();
    await tick();
    cycle.mode.onIntent('dependent'); // test
    cycle.mode.onIntent('connect');
    cycle.mode.onIntent('dependent'); // ship would make test depend on its own descendant
    await tick();
    expect(cycle.stdout.lastFrame()).toContain('CYCLE dependency');
    cycle.mode.onIntent('escape');
    cycle.mode.onIntent('connect'); // self is visibly invalid too
    await tick();
    expect(cycle.stdout.lastFrame()).toContain('INVALID dependency');
    cycle.close();
  });

  it('keeps invalid edits local, paints their issue, and refuses save and run', async () => {
    const bad = workflow({ stages: [stage('build', 'Build', [], { harness: null, model: null })] });
    const app = setup({ definition: bad });
    await tick();
    app.mode.onIntent('save');
    await tick();
    expect(app.stdout.lastFrame()).toContain('error');
    expect(app.stdout.lastFrame()).toContain('Stage harness is required.');
    expect(app.fake.commandCalls.filter((call) => call.name === 'workflow.put')).toHaveLength(0);

    app.mode.onIntent('run');
    await tick();
    expect(app.stdout.lastFrame()).toContain('Stage model is required.');
    expect(app.fake.commandCalls.filter((call) => call.name === 'workflow.start')).toHaveLength(0);
    expect(app.store.getState().workflows.items).toEqual([bad]);
    app.close();
  });

  it('selects and pans to the first blocking issue before refusing save', async () => {
    const stages = Array.from({ length: 8 }, (_, index) =>
      stage(
        index === 7 ? 'far-invalid' : `step-${index}`,
        index === 7 ? 'Far Invalid' : `Step ${index}`,
        index === 0 ? [] : [index === 7 ? 'step-6' : `step-${index - 1}`],
        index === 7 ? { harness: null } : {},
      ),
    );
    const app = setup({ definition: workflow({ stages }) });
    await tick();

    app.mode.onIntent('save');
    await tick();

    const frame = app.stdout.lastFrame();
    expect(frame).toContain('Stage harness is required.');
    expect(frame).toContain('ID: far-invalid');
    // The human name is in the canvas border; the machine ID remains available in the inspector.
    expect(frame).toContain('Far Invalid');
    app.close();
  });

  it('disables Run for reserved runtime values and clean unnamed drafts', async () => {
    const reserved = setup({
      definition: workflow({
        mode: 'generative',
        stages: [stage('build', 'Build', [], { gate: 'human' })],
      }),
    });
    reserved.mode.onIntent('run');
    await tick();
    expect(reserved.stdout.lastFrame()).toContain('not runnable yet');
    expect(
      reserved.fake.commandCalls.filter((call) => call.name === 'workflow.start'),
    ).toHaveLength(0);
    reserved.close();

    const unnamed = setup({ definition: workflow({ name: '', stages: [] }) });
    unnamed.mode.onIntent('run');
    await tick();
    expect(unnamed.stdout.lastFrame()).toContain('(unnamed)');
    expect(unnamed.fake.commandCalls.filter((call) => call.name === 'workflow.start')).toHaveLength(
      0,
    );
    unnamed.close();
  });

  it('renders immutable runtime snapshot geometry and current per-stage status over a dirty draft', async () => {
    const app = setup({ definition: workflow({ stages: [stage('local', 'Local')] }) });
    app.store.setState((state) => ({
      workflowRuns: {
        ...state.workflowRuns,
        activeRun: {
          workflow_id: 'run-9',
          definition_name: 'release',
          definition_version: 7,
          status: 'running',
          revision: 3,
          state: {
            schema_name: 'static_dag',
            schema_version: 1,
            value: { stages: [{ stage_id: 'frozen', status: 'running' }] },
          },
          created_at: '2026-07-27T00:00:00Z',
          updated_at: '2026-07-27T00:00:01Z',
          started_by: { kind: 'user', id: 'test' },
          correlation: { correlation_id: 'test' },
          definition_snapshot: workflow({ stages: [stage('frozen', 'Frozen')] }),
          stage_map: {},
        },
      },
    }));
    await tick();
    expect(app.stdout.lastFrame()).toContain('Monitoring immutable run snapshot v7');
    expect(app.stdout.lastFrame()).toContain('ID: frozen');
    expect(app.stdout.lastFrame()).toContain('Runtime: running');
    expect(app.stdout.lastFrame()).toContain('Run running · revision 3');
    expect(app.stdout.lastFrame()).not.toContain('ID: local');
    app.close();
  });

  it('projects every supported static-DAG stage status into the selected inspector', async () => {
    const statuses = [
      'blocked',
      'ready',
      'requested',
      'running',
      'waiting_approval',
      'succeeded',
      'failed',
      'cancelled',
    ] as const;
    const snapshot = workflow({
      stages: statuses.map((status) => stage(status, status)),
    });
    const app = setup({ definition: snapshot });
    app.store.setState((state) => ({
      workflowRuns: {
        ...state.workflowRuns,
        activeRun: {
          workflow_id: 'run-statuses',
          definition_name: 'release',
          definition_version: 2,
          status: 'running',
          revision: 4,
          state: {
            schema_name: 'static_dag',
            schema_version: 1,
            value: { stages: statuses.map((status) => ({ stage_id: status, status })) },
          },
          created_at: '2026-07-27T00:00:00Z',
          updated_at: '2026-07-27T00:00:01Z',
          started_by: { kind: 'user', id: 'test' },
          correlation: { correlation_id: 'test' },
          definition_snapshot: snapshot,
          stage_map: {},
        },
      },
    }));
    await tick();
    for (const status of statuses) {
      expect(app.stdout.lastFrame()).toContain(`Runtime: ${status}`);
      app.mode.onIntent('down');
      await tick();
    }
    app.close();
  });

  it('shows dirty/saving headers and runs only after a dirty draft is atomically saved', async () => {
    let resolvePut: ((result: unknown) => void) | undefined;
    const put = new Promise((resolve) => {
      resolvePut = resolve;
    });
    const app = setup({ put });
    await tick();
    app.mode.onIntent('enter');
    app.mode.onIntent('enter');
    app.mode.onUncaptured?.('!', {} as never);
    app.mode.onIntent('enter');
    app.mode.onIntent('escape');
    await tick();
    expect(app.stdout.lastFrame()).toContain('• unsaved');
    app.mode.onIntent('run');
    await tick();
    expect(app.stdout.lastFrame()).toContain('saving');
    expect(app.fake.commandCalls.filter((call) => call.name === 'workflow.start')).toHaveLength(0);
    resolvePut?.({
      ok: true,
      workflow: workflow({
        stages: [
          stage('build', 'Build!'),
          stage('test', 'Test', ['build']),
          stage('ship', 'Ship', ['test']),
        ],
      }),
      workflows: [workflow()],
      revision: 'r2',
    });
    await tick();
    await tick();
    expect(
      app.fake.commandCalls.find((call) => call.name === 'workflow.put')?.params,
    ).toMatchObject({ original_name: 'release', expected_revision: 'r1' });
    expect(app.fake.commandCalls.filter((call) => call.name === 'workflow.start')).toHaveLength(1);
    app.close();
  });

  it('opens the same vim-navigable stage editor with enter or i and accepts spaced descriptions', async () => {
    const app = setup();
    await tick();
    app.mode.onIntent('inspector');
    await tick();
    expect(app.stdout.lastFrame()).toContain('Stage editor');
    expect(app.stdout.lastFrame()).toContain('› Name: Build');
    app.mode.onIntent('down');
    await tick();
    expect(app.stdout.lastFrame()).toContain('› Description: —');
    app.mode.onIntent('enter');

    const capturedInputs = new Set(
      app.mode.keymap.flatMap((entry) =>
        (Array.isArray(entry.chord) ? entry.chord : [entry.chord]).flatMap((chord) =>
          chord.input === undefined ? [] : [chord.input],
        ),
      ),
    );
    expect(capturedInputs.has('a')).toBe(false);
    expect(capturedInputs.has('s')).toBe(false);
    for (const char of 'A useful description') app.mode.onUncaptured?.(char, {} as never);
    app.mode.onIntent('enter');
    await tick();

    expect(app.stdout.lastFrame()).toContain('│A useful description');
    app.mode.onIntent('up');
    app.mode.onIntent('enter');
    for (const char of ' stage') app.mode.onUncaptured?.(char, {} as never);
    app.mode.onIntent('enter');
    await tick();
    expect(app.stdout.lastFrame()).toContain('┌Build stage');
    app.mode.onIntent('escape');
    app.mode.onIntent('enter');
    await tick();
    expect(app.stdout.lastFrame()).toContain('Stage editor');
    app.close();
  });

  it('picks harness and model from option lists instead of free typing', async () => {
    const app = setup();
    await tick();
    app.mode.onIntent('inspector');
    app.mode.onIntent('down');
    app.mode.onIntent('down');
    await tick();
    expect(app.stdout.lastFrame()).toContain('› Harness: codex');

    app.mode.onIntent('enter');
    await tick();
    let frame = app.stdout.lastFrame();
    expect(frame).toContain('Options (↑/↓):');
    expect(frame).toContain('[codex]');
    expect(frame).toContain('claude_code');
    expect(app.mode.hints.some((hint) => hint.description === 'select option')).toBe(true);

    app.mode.onUncaptured?.('x', {} as never);
    await tick();
    expect(app.stdout.lastFrame()).toContain('› Harness: codex█');

    app.mode.onIntent('up'); // previous option → claude_code
    await tick();
    expect(app.stdout.lastFrame()).toContain('› Harness: claude_code█');
    app.mode.onIntent('down'); // codex
    app.mode.onIntent('down'); // cursor
    await tick();
    expect(app.stdout.lastFrame()).toContain('› Harness: cursor█');
    app.mode.onIntent('enter');
    await tick();
    expect(app.stdout.lastFrame()).toContain('› Harness: cursor');
    expect(app.stdout.lastFrame()).toMatch(/Model: (composer-2\.5|auto)/);

    app.mode.onIntent('down');
    app.mode.onIntent('enter');
    await tick();
    frame = app.stdout.lastFrame();
    expect(frame).toContain('Options (↑/↓):');
    expect(frame).toContain('composer-2.5');
    expect(frame).toContain('claude-sonnet-4.5');
    app.mode.onIntent('down');
    app.mode.onIntent('down');
    app.mode.onIntent('enter');
    await tick();
    expect(app.stdout.lastFrame()).toContain('› Model: gpt-5.5');
    app.close();
  });

  it('enters conflict mode and exposes reload, overwrite, and save-as actions without leaking the draft', async () => {
    const remote = workflow({ description: 'Remote', stages: [stage('remote', 'Remote')] });
    const app = setup({
      put: {
        ok: false,
        workflow: null,
        workflows: [remote],
        revision: 'r2',
        conflict: true,
        issues: [],
      },
      remote,
    });
    await tick();
    app.mode.onIntent('enter');
    app.mode.onIntent('enter');
    app.mode.onUncaptured?.('!', {} as never);
    app.mode.onIntent('enter');
    app.mode.onIntent('escape');
    app.mode.onIntent('save');
    await tick();
    expect(app.stdout.lastFrame()).toMatch(
      /Registry changed remotely.*Latest release v1 has 1 stage.*Review latest summary.*r: reload.*o: overwrite latest.*A: save as/s,
    );
    expect(app.store.getState().workflows.items[0]).toEqual(workflow());

    app.mode.onIntent('saveAs');
    await tick();
    expect(app.stdout.lastFrame()).toContain('name: release█');
    app.close();

    // Reload is deliberately only accepted while the conflict capture is active.
    const reloaded = setup({
      put: {
        ok: false,
        workflow: null,
        workflows: [remote],
        revision: 'r2',
        conflict: true,
        issues: [],
      },
      remote,
    });
    await tick();
    reloaded.mode.onIntent('enter');
    reloaded.mode.onIntent('enter');
    reloaded.mode.onUncaptured?.('!', {} as never);
    reloaded.mode.onIntent('enter');
    reloaded.mode.onIntent('escape');
    reloaded.mode.onIntent('save');
    await tick();
    reloaded.mode.onIntent('reload');
    await tick();
    expect(reloaded.stdout.lastFrame()).toContain('ID: remote');
    reloaded.close();

    let puts = 0;
    const overwrite = setup({
      put: () => {
        puts += 1;
        return puts === 1
          ? {
              ok: false,
              workflow: null,
              workflows: [remote],
              revision: 'r2',
              conflict: true,
              issues: [],
            }
          : { ok: true, workflow: workflow(), workflows: [workflow()], revision: 'r3' };
      },
      remote,
    });
    await tick();
    overwrite.mode.onIntent('enter');
    overwrite.mode.onIntent('enter');
    overwrite.mode.onUncaptured?.('!', {} as never);
    overwrite.mode.onIntent('enter');
    overwrite.mode.onIntent('escape');
    overwrite.mode.onIntent('save');
    await tick();
    overwrite.mode.onIntent('overwrite');
    await tick();
    await tick();
    expect(overwrite.fake.queryCalls.filter((call) => call.name === 'workflows.get')).toHaveLength(
      1,
    );
    expect(overwrite.fake.commandCalls.filter((call) => call.name === 'workflow.put')).toHaveLength(
      2,
    );
    expect(overwrite.stdout.lastFrame()).not.toContain('Registry changed remotely.');
    overwrite.close();
  });

  it('opens run-argument fields for placeholders, and isolates an abandoned draft from the registry', async () => {
    const definition = workflow({
      stages: [
        stage('build', 'Build {target}', [], { instructions: 'Deploy {target} to {region}' }),
      ],
    });
    const app = setup({ definition });
    await tick();
    app.mode.onIntent('run');
    await tick();
    expect(app.stdout.lastFrame()).toContain('Run arguments  [target=]  region=');
    app.mode.onUncaptured?.('p', {} as never);
    app.mode.onIntent('argsNext');
    app.mode.onUncaptured?.('u', {} as never);
    app.mode.onIntent('enter');
    await tick();
    expect(
      app.fake.commandCalls.find((call) => call.name === 'workflow.start')?.params,
    ).toMatchObject({ name: 'release', args: { target: 'p', region: 'u' } });

    app.mode.onIntent('enter');
    app.mode.onIntent('enter');
    app.mode.onUncaptured?.('!', {} as never);
    app.mode.onIntent('enter');
    app.mode.onIntent('escape');
    app.mode.onIntent('escape');
    await tick();
    expect(app.stdout.lastFrame()).toContain('Discard unsaved edits?');
    app.mode.onIntent('confirm');
    expect(app.store.getState().workflows.items).toEqual([definition]);
    app.close();
  });
});
