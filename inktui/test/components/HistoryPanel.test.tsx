/**
 * HistoryPanel test — the panel copy recipe, asserting the Pane + Ledger structure and the
 * history-specific keys (`a` toggle, `x` dismiss). Modeled on {@link ./NotesPanel.test.tsx}.
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { HistoryPanel } from '../../src/components/HistoryPanel.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import type { HistorySnapshotReply } from '../../src/store/history/historyActions.js';
import { createAppStore } from '../../src/store/store.js';

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

function recentIso(): string {
  // A timestamp a few minutes old so the row reads OPEN (not STALE) regardless of when the test runs.
  return new Date(Date.now() - 5 * 60 * 1000).toISOString();
}

function twoItems(): HistorySnapshotReply {
  return {
    invalidation_key: 'iv',
    items: [
      {
        item_id: 'collaborator:0',
        conversation_id: 'collaborator',
        text: 'fix the empty pane case',
        target: 'collaborator',
        ts: recentIso(),
        status: 'open',
        harness: null,
        conversation_status: 'in_progress',
        resumable: false,
      },
      {
        item_id: 'planner-foo:0',
        conversation_id: 'planner-foo',
        text: 'revisit worktree pruning',
        target: 'planner-foo',
        ts: recentIso(),
        status: 'open',
        harness: null,
        conversation_status: 'in_progress',
        resumable: false,
      },
    ],
  };
}

function Harness({
  store,
  inputStores,
}: {
  readonly store: ReturnType<typeof createAppStore>['store'];
  readonly inputStores: ReturnType<typeof createInputStores>;
}): JSX.Element {
  return (
    <AppStoreProvider value={store}>
      <InputStoresProvider value={inputStores}>
        <RootInput />
        <Box>
          <HistoryPanel />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

function RootInput(): null {
  useRootInput();
  return null;
}

async function setup(reply: HistorySnapshotReply = twoItems(), focused = true) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.history_snapshot', reply);
  fake.stubRpc('state.crow_snapshot', { invalidation_key: 'iv', sessions: [] });
  fake.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
  fake.stubRpc('command.status', {
    ok: true,
    status: 'done',
    result_json: JSON.stringify({ item_id: 'collaborator:0', status: 'dismissed' }),
  });
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.history.refresh();
  const inputStores = createInputStores(['history'], focused ? 'history' : 'chat');
  return { fake, store, dispose, inputStores };
}

describe('HistoryPanel', () => {
  it('renders the loose-thread digest title and multi-line entries', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // Pane inline title carries the loose-thread digest.
    expect(frame).toContain('┏━ History');
    expect(frame).toContain('2 loose');
    // Line 2 of each entry is the intention text.
    expect(frame).toContain('fix the empty pane case');
    expect(frame).toContain('revisit worktree pruning');
    // Status tag.
    expect(frame).toContain('OPEN');
    dispose();
  });

  it('toggles to the all-feed on `a` (title shows the mode)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(lastFrame() ?? '').not.toContain('· all');
    stdin.write('a');
    await tick();
    expect(lastFrame() ?? '').toContain('· all');
    dispose();
  });

  it('dismisses the cursor row on `x` (optimistic) when focused', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(store.getState().history.rows[0]?.status).toBe('open');
    stdin.write('x');
    await tick();
    expect(store.getState().history.rows[0]?.status).toBe('dismissed');
    dispose();
  });

  it('resumes the cursor row on `r` when resumable (submits agent.resume_from_history)', async () => {
    const resumableFirst: HistorySnapshotReply = {
      invalidation_key: 'iv',
      items: [
        {
          item_id: 'conv-uuid-1:0',
          // conversation_id (the resume key) is deliberately DIFFERENT from target (the agent id) —
          // this guards the fix: resume must send conversation_id, not the agent_id it used to.
          conversation_id: 'conv-uuid-1',
          text: 'resume me',
          target: 'crow-t1',
          ts: recentIso(),
          status: 'open',
          harness: 'claude_code',
          conversation_status: 'complete',
          resumable: true,
        },
      ],
    };
    const { fake, store, inputStores, dispose } = await setup(resumableFirst);
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    stdin.write('r');
    await tick();
    const submit = fake.rpcCalls.find((c) => c.method === 'command.submit');
    expect(submit?.params).toMatchObject({
      kind: 'agent.resume_from_history',
      payload: { conversation_id: 'conv-uuid-1' },
    });
    dispose();
  });

  it('does not resume on `r` for a non-resumable row (no resume command)', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    stdin.write('r');
    await tick();
    const resume = fake.rpcCalls.find(
      (c) =>
        c.method === 'command.submit' &&
        (c.params as { kind?: string }).kind === 'agent.resume_from_history',
    );
    expect(resume).toBeUndefined();
    dispose();
  });

  it('renders empty chrome when there are no loose threads', async () => {
    const { store, inputStores, dispose } = await setup({ invalidation_key: 'iv', items: [] }, true);
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(lastFrame() ?? '').toContain('no loose threads');
    dispose();
  });
});
