import { render } from 'ink-testing-library';
import { act, type JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../../src/bus/FakeBusClient.js';
import { TranscriptController } from '../../../src/components/panes/TranscriptController.js';
import { AppStoreProvider } from '../../../src/hooks/useAppStore.js';
import { BusClientProvider } from '../../../src/hooks/useBusClient.js';
import { InputStoresProvider } from '../../../src/hooks/useInputStores.js';
import { createInputStores } from '../../../src/input/createInputStores.js';
import { stageTranscriptFocusId } from '../../../src/input/focusIds.js';
import type { PanePresentation } from '../../../src/layout/paneLayoutTypes.js';
import type { AgentIdentity } from '../../../src/selectors/agentIdentity.js';
import type { ConversationBlock } from '../../../src/store/conversations/conversationsSlice.js';
import { type AppStoreApi, createAppStore } from '../../../src/store/store.js';

const presentation: PanePresentation = {
  width: 44,
  height: 8,
  focused: true,
};

const identity: AgentIdentity = {
  kind: 'collaborator',
  agentId: 'collab-1',
  label: 'collab',
};

const block: ConversationBlock = {
  type: 'assistant',
  id: '1',
  raw: {
    text: Array.from({ length: 10 }, (_, index) => `chat-line-${index + 1}`).join('\n'),
  },
};

async function flushReact(): Promise<void> {
  await act(async () => {});
}

function Harness({
  bus,
  store,
  inputStores,
}: {
  readonly bus: FakeBusClient;
  readonly store: AppStoreApi;
  readonly inputStores: ReturnType<typeof createInputStores>;
}): JSX.Element {
  return (
    <BusClientProvider value={bus}>
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
    </BusClientProvider>
  );
}

describe('TranscriptController', () => {
  it('treats scroll-up as older history and scroll-down as newer history for keys and wheel', async () => {
    const fake = new FakeBusClient();
    const { store, dispose } = createAppStore(fake);
    store.setState((state) => ({
      roster: {
        rows: [
          {
            agentId: 'collab-1',
            role: 'collaborator',
            ticketId: null,
            ticketTitle: null,
            harness: 'claude',
            model: 'opus',
            status: 'idle',
            session: 'murder_repo_collaborator',
            worktreePath: null,
          },
        ],
        status: 'ready',
        error: null,
      },
      conversations: {
        ...state.conversations,
        transcripts: { 'collab-1': [block] },
      },
    }));
    const focusId = stageTranscriptFocusId('collab-1');
    const inputStores = createInputStores([], focusId);
    const tree = render(<Harness bus={fake} store={store} inputStores={inputStores} />);
    await flushReact();
    await flushReact();

    expect(tree.lastFrame() ?? '').toContain('chat-line-10');

    await act(async () => {
      inputStores.keymaps.getState().keymaps[focusId]?.onIntent('scrollUp');
    });
    expect(tree.lastFrame() ?? '').toContain('chat-line-4');
    expect(tree.lastFrame() ?? '').not.toContain('chat-line-10');

    await act(async () => {
      inputStores.paneScroll.emit(focusId, 'down', 1);
    });
    expect(tree.lastFrame() ?? '').toContain('chat-line-10');

    await act(async () => {
      inputStores.paneScroll.emit(focusId, 'up', 2);
    });
    expect(tree.lastFrame() ?? '').toContain('chat-line-3');
    expect(tree.lastFrame() ?? '').not.toContain('chat-line-10');

    tree.unmount();
    dispose();
  });
});
