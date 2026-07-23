import { render } from 'ink-testing-library';
import { act, type JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeApplicationClient } from '../../../src/application/FakeApplicationClient.js';
import { DocumentController } from '../../../src/components/panes/DocumentController.js';
import { AppStoreProvider } from '../../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../../src/hooks/useInputStores.js';
import { createInputStores } from '../../../src/input/createInputStores.js';
import { stageDocFocusId } from '../../../src/input/focusIds.js';
import type { PanePresentation } from '../../../src/layout/paneLayoutTypes.js';
import { type AppStoreApi, createAppStore } from '../../../src/store/store.js';

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
  store,
  inputStores,
}: {
  readonly store: AppStoreApi;
  readonly inputStores: ReturnType<typeof createInputStores>;
}): JSX.Element {
  return (
    <AppStoreProvider value={store}>
      <InputStoresProvider value={inputStores}>
        <DocumentController presentation={presentation} open={{ kind: 'plan', name: 'scroll' }} />
      </InputStoresProvider>
    </AppStoreProvider>
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
    const tree = render(<Harness store={store} inputStores={inputStores} />);
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
    const tree = render(<Harness store={store} inputStores={inputStores} />);
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
});
