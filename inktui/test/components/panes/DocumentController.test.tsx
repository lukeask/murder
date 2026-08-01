import { MouseProvider } from '@ink-tools/ink-mouse';
import { render } from 'ink-testing-library';
import { act, type JSX } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { FakeApplicationClient } from '@murder/ui-core/application/FakeApplicationClient.js';
import { DocumentController } from '../../../src/components/panes/DocumentController.js';
import { ApplicationClientProvider } from '@murder/ui-core/hooks/useApplicationClient.js';
import { AppStoreProvider } from '@murder/ui-core/hooks/useAppStore.js';
import { InputStoresProvider } from '../../../src/hooks/useInputStores.js';
import { createInputStores } from '../../../src/input/createInputStores.js';
import { CHAT_FOCUS, stageDocFocusId } from '@murder/ui-core/input/focusIds.js';
import { selectActiveMode } from '../../../src/input/modeStore.js';
import type { PanePresentation } from '../../../src/layout/paneLayoutTypes.js';
import { type AppStoreApi, createAppStore } from '@murder/ui-core/store/store.js';

const presentation: PanePresentation = {
  width: 42,
  height: 6,
  focused: true,
};

const body = Array.from({ length: 10 }, (_, index) => `doc-line-${index + 1}`).join('\n');

async function flushReact(): Promise<void> {
  await act(async () => {});
}

function Harness({
  bus,
  store,
  inputStores,
  panePresentation = presentation,
}: {
  readonly bus: FakeApplicationClient;
  readonly store: AppStoreApi;
  readonly inputStores: ReturnType<typeof createInputStores>;
  readonly panePresentation?: PanePresentation;
}): JSX.Element {
  return (
    <MouseProvider autoEnable={false}>
      <ApplicationClientProvider value={bus}>
        <AppStoreProvider value={store}>
          <InputStoresProvider value={inputStores}>
            <DocumentController
              presentation={panePresentation}
              open={{ kind: 'plan', name: 'scroll' }}
            />
          </InputStoresProvider>
        </AppStoreProvider>
      </ApplicationClientProvider>
    </MouseProvider>
  );
}

describe('DocumentController', () => {
  it('scrolls, jumps to goto lines, and handles wheel commands within the document window', async () => {
    const fake = new FakeApplicationClient();
    const { store, dispose } = createAppStore(fake);
    store.setState({
      docView: {
        open: { kind: 'plan', name: 'scroll' },
        body,
        status: 'ready',
        error: null,
      },
    });
    const focusId = stageDocFocusId('scroll');
    const inputStores = createInputStores([], focusId);
    const tree = render(<Harness bus={fake} store={store} inputStores={inputStores} />);
    await flushReact();

    expect(tree.lastFrame() ?? '').toContain('doc-line-1');

    await act(async () => {
      inputStores.keymaps.getState().keymaps[focusId]?.onIntent('scrollDown');
    });
    expect(tree.lastFrame() ?? '').toContain('doc-line-2');
    expect(tree.lastFrame() ?? '').not.toContain('doc-line-1');

    await act(async () => {
      inputStores.keymaps.getState().keymaps[focusId]?.onIntent('goto.start');
    });
    await act(async () => {
      inputStores.keymaps.getState().keymaps[focusId]?.onIntent('goto.digit.7');
    });
    expect(tree.lastFrame() ?? '').toContain('doc-line-7');
    expect(tree.lastFrame() ?? '').toContain('doc-line-10');

    await act(async () => {
      inputStores.paneScroll.emit(focusId, 'up', 2);
    });
    expect(tree.lastFrame() ?? '').toContain('doc-line-5');
    expect(tree.lastFrame() ?? '').not.toContain('doc-line-10');

    await act(async () => {
      inputStores.paneScroll.emit(focusId, 'down', 2);
    });
    expect(tree.lastFrame() ?? '').toContain('doc-line-10');

    tree.unmount();
    dispose();
  });

  it('updates immediately on display-mode changes and clamps the persisted row scroll', async () => {
    const fake = new FakeApplicationClient();
    const { store, dispose } = createAppStore(fake);
    const table = [
      '| First heading | Second heading | Third heading | Fourth heading |',
      '| --- | --- | --- | --- |',
      '| alpha | beta | gamma | delta |',
      '| one | two | three | four |',
    ].join('\n');
    store.setState((state) => ({
      settings: { ...state.settings, documentDisplayMode: 'markdown' },
      docView: {
        open: { kind: 'plan', name: 'mode' },
        body: table,
        status: 'ready',
        error: null,
      },
    }));
    const focusId = stageDocFocusId('scroll');
    const inputStores = createInputStores([], focusId);
    const tree = render(<Harness bus={fake} store={store} inputStores={inputStores} />);
    await flushReact();

    inputStores.paneUi.getState().setScroll(focusId, 99);
    await flushReact();
    const markdownScroll = inputStores.paneUi.getState().scrolls[focusId] ?? 0;
    expect(markdownScroll).toBeGreaterThan(0);
    expect(tree.lastFrame() ?? '').toContain('Fourth heading:');

    await act(async () => {
      store.setState((state) => ({
        settings: { ...state.settings, documentDisplayMode: 'plain' },
      }));
    });
    await flushReact();

    expect(tree.lastFrame() ?? '').toContain('|');
    expect(tree.lastFrame() ?? '').not.toContain('Fourth heading:');
    expect(inputStores.paneUi.getState().scrolls[focusId] ?? 0).toBeLessThan(markdownScroll);

    tree.unmount();
    dispose();
  });

  it('starts, displays, drives, resizes, and exits a document editor session', async () => {
    vi.useFakeTimers();
    try {
      const fake = new FakeApplicationClient();
      fake.stubCommand('document.editor.start', {
        status: 'active',
        document_path: '/repo/.murder/plans/scroll.md',
        terminal_session_id: '00000000-0000-0000-0000-000000000123',
        reused: false,
      });
      fake.stubCommand('document.editor.input', { handled: true });
      fake.stubCommand('document.editor.resize', { handled: true });
      fake.stubCommand('document.editor.status', {
        terminal_session_id: '00000000-0000-0000-0000-000000000123',
        document_path: '/repo/.murder/plans/scroll.md',
        status: 'exited',
      });
      const { store, dispose } = createAppStore(fake);
      store.setState({
        docView: {
          open: { kind: 'plan', name: 'scroll' },
          body,
          status: 'ready',
          error: null,
        },
      });
      const focusId = stageDocFocusId('scroll');
      const inputStores = createInputStores([], focusId);
      inputStores.focus.getState().measure(focusId, { x: 20, y: 0, width: 42, height: 6 });
      const tree = render(<Harness bus={fake} store={store} inputStores={inputStores} />);
      await flushReact();

      await act(async () => {
        inputStores.keymaps.getState().keymaps[focusId]?.onIntent('edit');
        await Promise.resolve();
      });
      await flushReact();

      expect(fake.commandCalls[0]).toEqual({
        name: 'document.editor.start',
        params: { kind: 'plan', name: 'scroll', columns: 40, rows: 4 },
      });
      expect(fake.terminalAttachCalls).toEqual([
        { sessionId: '00000000-0000-0000-0000-000000000123' },
      ]);
      expect(fake.terminalSubscriberCount).toBe(1);

      await act(async () => {
        fake.emitTerminal(
          '00000000-0000-0000-0000-000000000123',
          '\u001B[31mVIM editor buffer\u001B[0m',
        );
      });
      expect(tree.lastFrame() ?? '').toContain('VIM editor buffer');

      const mode = selectActiveMode(inputStores.modes);
      expect(mode).not.toBeNull();
      inputStores.focus.getState().focus(CHAT_FOCUS);
      expect(mode?.onUncaptured?.('x', {} as never)).toBe(false);
      expect(mode?.onUncaptured?.('h', { ctrl: true } as never)).toBe(false);
      inputStores.focus.getState().focus(focusId);
      expect(mode?.onUncaptured?.('h', { ctrl: true } as never)).toBe(true);
      expect(mode?.onUncaptured?.('l', { meta: true } as never)).toBe(true);
      expect(mode?.onUncaptured?.('', { escape: true } as never)).toBe(true);
      expect(mode?.onUncaptured?.('c', { ctrl: true } as never)).toBe(true);
      expect(mode?.onUncaptured?.('x', { ctrl: true } as never)).toBe(true);
      expect(mode?.onUncaptured?.('q', { meta: true } as never)).toBe(true);
      await flushReact();
      expect(
        fake.commandCalls
          .filter((call) => call.name === 'document.editor.input')
          .map((call) => call.params),
      ).toEqual([]);

      tree.rerender(
        <Harness
          bus={fake}
          store={store}
          inputStores={inputStores}
          panePresentation={{ ...presentation, width: 50, height: 8 }}
        />,
      );
      tree.rerender(
        <Harness
          bus={fake}
          store={store}
          inputStores={inputStores}
          panePresentation={{ ...presentation, width: 60, height: 12 }}
        />,
      );
      await act(async () => {
        vi.advanceTimersByTime(100);
        await Promise.resolve();
      });
      expect(fake.commandCalls.filter((call) => call.name === 'document.editor.resize')).toEqual([
        {
          name: 'document.editor.resize',
          params: {
            terminal_session_id: '00000000-0000-0000-0000-000000000123',
            columns: 58,
            rows: 10,
          },
        },
      ]);

      await act(async () => {
        vi.advanceTimersByTime(400);
        await Promise.resolve();
      });
      await flushReact();
      expect(selectActiveMode(inputStores.modes)).toBeNull();
      expect(fake.terminalSubscriberCount).toBe(0);
      expect(tree.lastFrame() ?? '').toContain('doc-line-1');

      tree.unmount();
      dispose();
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps launch errors visible in the ordinary document pane', async () => {
    const fake = new FakeApplicationClient();
    fake.stubCommand('document.editor.start', () => {
      throw new Error('no editor configured; set $VISUAL or $EDITOR');
    });
    const { store, dispose } = createAppStore(fake);
    store.setState({
      docView: {
        open: { kind: 'plan', name: 'scroll' },
        body,
        status: 'ready',
        error: null,
      },
    });
    const focusId = stageDocFocusId('scroll');
    const inputStores = createInputStores([], focusId);
    const tree = render(<Harness bus={fake} store={store} inputStores={inputStores} />);
    await flushReact();

    await act(async () => {
      inputStores.keymaps.getState().keymaps[focusId]?.onIntent('edit');
      await Promise.resolve();
    });
    await flushReact();

    expect(tree.lastFrame() ?? '').toContain('error: no editor configured');
    expect(fake.terminalSubscriberCount).toBe(0);

    tree.unmount();
    dispose();
  });
});
