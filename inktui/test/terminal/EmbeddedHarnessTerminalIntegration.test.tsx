import { EventEmitter } from 'node:events';
import { MouseProvider } from '@ink-tools/ink-mouse';
import { render } from 'ink-testing-library';
import { act, type JSX } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { TerminalFrame, TerminalUpdate } from '../../src/application/ApplicationClient.js';
import { FakeApplicationClient } from '../../src/application/FakeApplicationClient.js';
import { TranscriptController } from '../../src/components/panes/TranscriptController.js';
import { ApplicationClientProvider } from '../../src/hooks/useApplicationClient.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { stageTranscriptFocusId } from '../../src/input/focusIds.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import type { PanePresentation } from '../../src/layout/paneLayoutTypes.js';
import type { AgentIdentity } from '../../src/selectors/agentIdentity.js';
import { type AppStoreApi, createAppStore } from '../../src/store/store.js';
import { StdinShim } from '../../src/terminal/StdinShim.js';

const SESSION_ID = '00000000-0000-0000-0000-000000000456';
const identity: AgentIdentity = {
  kind: 'collaborator',
  agentId: 'collab-terminal',
  label: 'collab terminal',
  sessionId: SESSION_ID,
};

async function flushReact(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

function Harness({
  bus,
  store,
  inputStores,
  presentation,
}: {
  readonly bus: FakeApplicationClient;
  readonly store: AppStoreApi;
  readonly inputStores: ReturnType<typeof createInputStores>;
  readonly presentation: PanePresentation;
}): JSX.Element {
  return (
    <MouseProvider autoEnable={false}>
      <ApplicationClientProvider value={bus}>
        <AppStoreProvider value={store}>
          <InputStoresProvider value={inputStores}>
            <TranscriptController
              presentation={presentation}
              identity={identity}
              state={store.getState()}
              activeRecipientTarget={false}
            />
          </InputStoresProvider>
        </AppStoreProvider>
      </ApplicationClientProvider>
    </MouseProvider>
  );
}

function fixedHarnessFrame(): TerminalUpdate {
  const rows = Array.from(
    { length: 50 },
    (_, row) => `${String(row).padStart(2, '0')}:${'x'.repeat(8)}`,
  );
  const frame: TerminalFrame = {
    type: 'terminal.frame',
    subscription_id: 'integration-terminal',
    sequence: 1,
    session_id: SESSION_ID,
    captured_at: new Date(0).toISOString(),
    columns: 220,
    rows: 50,
    encoding: 'utf-8',
    data: rows.join('\n'),
    reset: true,
  };
  return frame;
}

describe('embedded harness terminal acceptance', () => {
  afterEach(() => vi.useRealTimers());

  it('routes direct input in order, fences ownership, reserves navigation, and preserves 220×50 geometry', async () => {
    vi.useFakeTimers();
    const bus = new FakeApplicationClient();
    const acquire = vi.spyOn(bus, 'openTerminalInput');
    const { store, dispose } = createAppStore(bus);
    store.setState((state) => ({
      conversations: {
        ...state.conversations,
        paneViewModes: { ...state.conversations.paneViewModes, [identity.agentId]: 'tmux' },
      },
    }));
    const focusId = stageTranscriptFocusId(identity.agentId);
    const inputStores = createInputStores([], focusId);
    inputStores.focus.getState().measure(focusId, { x: 0, y: 0, width: 100, height: 25 });
    const tree = render(
      <Harness
        bus={bus}
        store={store}
        inputStores={inputStores}
        presentation={{ width: 100, height: 25, focused: true }}
      />,
    );
    await flushReact();
    act(() => bus.emitTerminalUpdate(SESSION_ID, fixedHarnessFrame()));
    await flushReact();

    expect(acquire).toHaveBeenCalledWith(SESSION_ID);
    expect(tree.lastFrame()).toContain('[interactive]');
    expect(tree.lastFrame()).toContain('49:');
    expect(tree.lastFrame()).toContain('/220×50');
    const mode = selectActiveMode(inputStores.modes);
    if (mode?.stdinRoute?.kind !== 'terminal') throw new Error('raw terminal route missing');

    const realStdin = new EventEmitter();
    const stdin = new StdinShim(realStdin);
    stdin.setRoute(mode.stdinRoute);
    const navigate = vi.spyOn(inputStores.focus.getState(), 'navigate');
    const ordinary = Buffer.from('ordinary');
    const nonNavigationCtrl = Buffer.from([0x05]);
    const nonNavigationAlt = Buffer.from('\u001bx');
    const rapid = Buffer.from(
      Array.from({ length: 200 }, (_, index) => String(index % 10)).join(''),
    );
    const paste = Buffer.from('\u001b[200~pasted\nbytes\tunchanged\u001b[201~');
    const reserved = [
      Buffer.from([0x08]),
      Buffer.from([0x0a]),
      Buffer.from([0x0b]),
      Buffer.from([0x0c]),
      Buffer.from('\u001bh'),
      Buffer.from('\u001bj'),
      Buffer.from('\u001bk'),
      Buffer.from('\u001bl'),
    ];
    for (const bytes of [
      ordinary,
      nonNavigationCtrl,
      nonNavigationAlt,
      ...reserved,
      rapid,
      paste,
    ]) {
      realStdin.emit('data', bytes);
    }
    await flushReact();

    const delivered = Buffer.concat(
      bus.terminalInputCalls.map((call) => Buffer.from(call.data, 'base64')),
    );
    expect(delivered).toEqual(
      Buffer.concat([ordinary, nonNavigationCtrl, nonNavigationAlt, rapid, paste]),
    );
    expect(navigate.mock.calls.map(([direction]) => direction)).toEqual([
      'left',
      'down',
      'up',
      'right',
      'left',
      'down',
      'up',
      'right',
    ]);

    inputStores.paneScroll.emitTerminalViewport(focusId, {
      kind: 'pan',
      deltaColumns: 20,
      deltaRows: -5,
    });
    tree.rerender(
      <Harness
        bus={bus}
        store={store}
        inputStores={inputStores}
        presentation={{ width: 42, height: 10, focused: true }}
      />,
    );
    await flushReact();
    expect(acquire).toHaveBeenCalledTimes(1);
    expect(bus.commandCalls.some((call) => call.name === 'document.editor.resize')).toBe(false);
    expect(fixedHarnessFrame()).toMatchObject({ columns: 220, rows: 50 });

    await act(async () => {
      vi.advanceTimersByTime(5_000);
      await Promise.resolve();
    });
    expect(bus.terminalInputLeaseActions.some((action) => action.kind === 'renew')).toBe(true);

    act(() =>
      bus.emitTerminalInputFailure(
        `fake-terminal-input:${SESSION_ID}`,
        'writer lease owned by another client',
      ),
    );
    await flushReact();
    tree.rerender(
      <Harness
        bus={bus}
        store={store}
        inputStores={inputStores}
        presentation={{ width: 100, height: 25, focused: true }}
      />,
    );
    await flushReact();
    expect(tree.lastFrame()).toContain('read-only');
    expect(selectActiveMode(inputStores.modes)).toBeNull();
    expect(bus.terminalInputLeaseActions.some((action) => action.kind === 'close')).toBe(true);

    stdin.dispose();
    tree.unmount();
    dispose();
  });
});
