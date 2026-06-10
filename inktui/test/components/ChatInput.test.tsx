/**
 * Chat-input send tests — the persistent chat-input mode (C11, part F).
 *
 * Drives the full keystroke→send pipeline through the ONE root dispatcher (rule 5: no second
 * `useInput` — these tests render `ChatInput` + the root loop and type into stdin, exactly as a
 * user would). Covers the DoD requirements:
 *  - type → the buffer renders live in the ChatInput.
 *  - enter → `agent.message` fires with the active agent_id, and the buffer clears.
 *  - a global chord (`alt+s` → spawn handler) still fires while the buffer has text (proving layer
 *    1 preempts the layer-2 chat handler — the persistent mode never swallows global chords).
 *  - backspace deletes from the buffer.
 */

import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { makeChatInputHandler } from '../../src/components/App.js';
import { ChatInput } from '../../src/components/ChatInput.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { createImageDraftStore } from '../../src/store/imageDraft/imageDraftStore.js';
import { createAppStore } from '../../src/store/store.js';

const RETURN = '\r';
const ALT_S = '\x1bs';
const BACKSPACE = '\x7f';

async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** A roster with one collaborator (the default active chat target). */
const ROSTER_REPLY = {
  invalidation_key: 'iv',
  sessions: [
    {
      agent_id: 'collab-1',
      role: 'collaborator',
      ticket_id: null,
      ticket_title: null,
      harness: 'claude',
      model: 'anthropic/claude-opus',
      status: 'idle',
      session: 'collaborator',
    },
  ],
};

function Harness({
  store,
  inputStores,
  spawn,
}: {
  readonly store: ReturnType<typeof createAppStore>['store'];
  readonly inputStores: ReturnType<typeof createInputStores>;
  readonly spawn?: () => void;
}): JSX.Element {
  function Root(): null {
    // Wire the persistent chat handler exactly as App.tsx's Shell does. F9: an image-draft store is
    // threaded in (no images pasted in these tests, so a bare FakeBusClient-backed one suffices).
    const imageDraft = createImageDraftStore(new FakeBusClient());
    useRootInput({
      ...(spawn !== undefined ? { spawn } : {}),
      chatInput: makeChatInputHandler(inputStores.chatInput, store, imageDraft),
    });
    return null;
  }
  return (
    <AppStoreProvider value={store}>
      <InputStoresProvider value={inputStores}>
        <Root />
        <ChatInput />
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

async function setup() {
  const fake = new FakeBusClient();
  fake.stubRpc('state.crow_snapshot', ROSTER_REPLY);
  // F2: chat sends route through command.submit (agent.message command kind), not a direct RPC.
  fake.stubRpc('command.submit', { ok: true, command_id: 'cmd-1' });
  fake.stubRpc('command.status', { ok: true, status: 'done', result_json: '{}' });
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.roster.refresh();
  // Chat is the focus home — no visible panels, focus 'chat'.
  const inputStores = createInputStores([], 'chat');
  return { fake, store, dispose, inputStores };
}

describe('ChatInput — persistent chat-input send (C11)', () => {
  it('types into the buffer and renders it live', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame, stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write('hi');
    await tick();
    expect(inputStores.chatInput.getState().text).toBe('hi');
    expect(lastFrame() ?? '').toContain('hi');
    dispose();
  });

  it('renders the target on the top border as → <label>, with ★ when favorited (item 2)', async () => {
    // The only roster row is a collaborator (default-favorited) → starred target on the border.
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('→ ★ collab-1');
    // The dropped `›` prompt is gone from the border.
    expect(frame).not.toContain('─ ›');
    dispose();
  });

  it('enter sends the buffer to the active agent and clears it', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write('hello there');
    await tick();
    stdin.write(RETURN);
    await tick();

    const sendCalls = fake.rpcCalls.filter(
      (c) =>
        c.method === 'command.submit' && (c.params as { kind: string }).kind === 'agent.message',
    );
    expect(sendCalls.length).toBe(1);
    expect(sendCalls[0]?.params).toMatchObject({
      kind: 'agent.message',
      payload: { agent_id: 'collab-1', message: 'hello there' },
    });
    // Buffer cleared after send.
    expect(inputStores.chatInput.getState().text).toBe('');
    dispose();
  });

  it('enter on an empty buffer is a no-op (no send)', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write(RETURN);
    await tick();
    expect(fake.rpcCalls.filter((c) => c.method === 'command.submit').length).toBe(0);
    dispose();
  });

  it('backspace deletes from the buffer', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write('abc');
    await tick();
    stdin.write(BACKSPACE);
    await tick();
    expect(inputStores.chatInput.getState().text).toBe('ab');
    dispose();
  });

  it('a global chord (alt+s) STILL fires while the chat buffer has text (layer 1 > layer 2)', async () => {
    const { store, inputStores, dispose } = await setup();
    const spawnFn = vi.fn();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} spawn={spawnFn} />);
    await tick();

    // Type a partial message, then hit alt+s — the global chord must preempt the chat handler.
    stdin.write('draft message');
    await tick();
    stdin.write(ALT_S);
    await tick();

    expect(spawnFn).toHaveBeenCalledOnce();
    // The buffer is untouched — alt+s did not get appended or cleared.
    expect(inputStores.chatInput.getState().text).toBe('draft message');
    dispose();
  });
});
