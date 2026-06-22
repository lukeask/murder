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
import type { CommandCtx } from '../../src/input/commandDispatch.js';
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
    // Workstream E: the chat handler now takes a CommandCtx for the `:`/`/` prefix dispatcher. These
    // tests never type a prefix, so a no-op ctx is sufficient (the dispatcher returns false → normal
    // send path runs, exactly as before).
    const commandCtx: CommandCtx = {
      sendKey: () => {},
      clearTranscript: () => {},
      openHelp: () => {},
      captureNote: () => {},
      saveTemplate: () => {},
      setPaneViewMode: () => {},
      pushToast: () => 0,
    };
    useRootInput({
      ...(spawn !== undefined ? { spawn } : {}),
      chatInput: makeChatInputHandler(
        inputStores.chatInput,
        store,
        imageDraft,
        commandCtx,
        inputStores.chatHistory,
        inputStores.chatVim,
        inputStores.focus,
      ),
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

  it('renders the target on the top border as ▸ <label>, with ★ when favorited (item 2)', async () => {
    // The only roster row is a collaborator (default-favorited) → starred target on the border.
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('▸ ★ collab-1');
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

// ---------------------------------------------------------------------------
// Multiple-choice takeover + queued-message line
// ---------------------------------------------------------------------------

const DOWN_ARROW = '\x1b[B';

/** Seed a LIVE (trailing, unanswered) choice_prompt into the collaborator's transcript. */
function seedChoicePrompt(
  store: ReturnType<typeof createAppStore>['store'],
  { multi = false, selected = 1 }: { multi?: boolean; selected?: number | null } = {},
): void {
  store.setState((state) => ({
    conversations: {
      ...state.conversations,
      transcripts: {
        ...state.conversations.transcripts,
        'collab-1': [
          {
            type: 'choice_prompt',
            id: '7',
            raw: {
              type: 'choice_prompt',
              question: 'Which color?',
              options: [
                { number: 1, label: 'Red', description: 'Warm', checked: multi ? false : null },
                { number: 2, label: 'Blue', description: 'Cool', checked: multi ? true : null },
              ],
              footer: 'Enter to select',
              selected,
              answered: false,
              chosen: null,
              multi,
            },
          },
        ],
      },
    },
  }));
}

/** Seed a LIVE single-select choice with a freeform "Type something." option, cursor on it by
 * default — the state that triggers the local-compose takeover. */
function seedFreeformChoicePrompt(
  store: ReturnType<typeof createAppStore>['store'],
  { selected = 3 }: { selected?: number } = {},
): void {
  store.setState((state) => ({
    conversations: {
      ...state.conversations,
      transcripts: {
        ...state.conversations.transcripts,
        'collab-1': [
          {
            type: 'choice_prompt',
            id: '8',
            raw: {
              type: 'choice_prompt',
              question: 'Want a fallback binding too?',
              options: [
                { number: 1, label: 'ctrl+enter + alt+enter', description: null, checked: null },
                { number: 2, label: 'ctrl+enter only', description: null, checked: null },
                { number: 3, label: 'Type something.', description: null, checked: null },
              ],
              footer: 'Enter to select',
              selected,
              answered: false,
              chosen: null,
              multi: false,
            },
          },
        ],
      },
    },
  }));
}

/** Seed a queued-but-undelivered message for the collaborator. */
function seedQueued(store: ReturnType<typeof createAppStore>['store'], message: string): void {
  store.setState((state) => ({
    conversations: {
      ...state.conversations,
      meta: {
        ...state.conversations.meta,
        'collab-1': { liveState: 'working', queuedMessage: message },
      },
    },
  }));
}

function submitsOfKind(fake: FakeBusClient, kind: string) {
  return fake.rpcCalls.filter(
    (c) => c.method === 'command.submit' && (c.params as { kind: string }).kind === kind,
  );
}

describe('ChatInput — multiple-choice takeover', () => {
  it('renders the live choice menu in place of the text input', async () => {
    const { store, inputStores, dispose } = await setup();
    seedChoicePrompt(store);
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('Which color?');
    expect(frame).toContain('❯ 1. Red');
    expect(frame).toContain('2. Blue');
    expect(frame).toContain('· choice');
    expect(frame).not.toContain('type a message');
    dispose();
  });

  it('renders checkboxes on a multi-select menu', async () => {
    const { store, inputStores, dispose } = await setup();
    seedChoicePrompt(store, { multi: true });
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('[ ] Red');
    expect(frame).toContain('[✔] Blue');
    expect(frame).toContain('space toggle');
    dispose();
  });

  it('forwards arrows/enter/digits to the agent pane instead of buffering', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    seedChoicePrompt(store);
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write(DOWN_ARROW);
    await tick();
    stdin.write('2');
    await tick();
    stdin.write(RETURN);
    await tick();

    const keys = submitsOfKind(fake, 'agent.send_key').map(
      (c) => (c.params as { payload: { key: string; literal: boolean } }).payload,
    );
    expect(keys).toEqual([
      { agent_id: 'collab-1', key: 'Down', literal: false, enter: false },
      { agent_id: 'collab-1', key: '2', literal: true, enter: false },
      { agent_id: 'collab-1', key: 'Enter', literal: false, enter: false },
    ]);
    // Nothing was buffered and no chat message was sent.
    expect(inputStores.chatInput.getState().text).toBe('');
    expect(submitsOfKind(fake, 'agent.message').length).toBe(0);
    dispose();
  });

  it('renders the multi-select Submit row, uncursored while an option is selected', async () => {
    const { store, inputStores, dispose } = await setup();
    seedChoicePrompt(store, { multi: true });
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('❯ 1. [ ] Red');
    expect(frame).toMatch(/ {2}Submit/);
    expect(frame).not.toContain('❯ Submit');
    dispose();
  });

  it('keeps the takeover live with the cursor on Submit (selected: null) and forwards enter', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    seedChoicePrompt(store, { multi: true, selected: null });
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    // Cursor on the Submit row, no option cursored — and the takeover is still engaged.
    expect(frame).toContain('❯ Submit');
    expect(frame).not.toContain('❯ 1.');
    expect(frame).not.toContain('type a message');

    stdin.write(RETURN);
    await tick();
    const keys = submitsOfKind(fake, 'agent.send_key').map(
      (c) => (c.params as { payload: { key: string } }).payload.key,
    );
    expect(keys).toEqual(['Enter']);
    expect(submitsOfKind(fake, 'agent.message').length).toBe(0);
    dispose();
  });

  it('forwards space as the multi-select toggle key', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    seedChoicePrompt(store, { multi: true });
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    stdin.write(' ');
    await tick();
    const keys = submitsOfKind(fake, 'agent.send_key').map(
      (c) => (c.params as { payload: { key: string } }).payload.key,
    );
    expect(keys).toEqual(['Space']);
    dispose();
  });

  it('freeform option: buffers typed chars LOCALLY without per-key sends, and echoes them inline', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    seedFreeformChoicePrompt(store);
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write('a b3');
    await tick();
    // Typed into the local buffer (space included), not round-tripped to the pane.
    expect(inputStores.chatInput.getState().text).toBe('a b3');
    expect(submitsOfKind(fake, 'agent.send_key').length).toBe(0);
    // The draft is echoed inline on the freeform row (with the caret), not the placeholder label.
    const frame = lastFrame() ?? '';
    expect(frame).toContain('a b3');
    expect(frame).not.toContain('3. Type something.');
    dispose();
  });

  it('freeform option: enter flushes the whole draft + newline in ONE literal send, then clears', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    seedFreeformChoicePrompt(store);
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    stdin.write('my custom answer');
    await tick();
    stdin.write(RETURN);
    await tick();

    const keys = submitsOfKind(fake, 'agent.send_key').map(
      (c) => (c.params as { payload: { key: string; literal: boolean } }).payload,
    );
    // Exactly one send: the full answer + newline, literal. No per-character round-trips.
    expect(keys).toEqual([
      { agent_id: 'collab-1', key: 'my custom answer\n', literal: true, enter: false },
    ]);
    expect(inputStores.chatInput.getState().text).toBe('');
    expect(submitsOfKind(fake, 'agent.message').length).toBe(0);
    dispose();
  });

  it('freeform option: backspace edits the local draft (no pane send)', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    seedFreeformChoicePrompt(store);
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    stdin.write('abc');
    await tick();
    stdin.write(BACKSPACE);
    await tick();
    expect(inputStores.chatInput.getState().text).toBe('ab');
    expect(submitsOfKind(fake, 'agent.send_key').length).toBe(0);
    dispose();
  });

  it('freeform option: arrow nav abandons the draft and forwards the key to the pane', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    seedFreeformChoicePrompt(store);
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    stdin.write('half-typed');
    await tick();
    stdin.write(DOWN_ARROW);
    await tick();
    expect(inputStores.chatInput.getState().text).toBe('');
    const keys = submitsOfKind(fake, 'agent.send_key').map(
      (c) => (c.params as { payload: { key: string } }).payload.key,
    );
    expect(keys).toEqual(['Down']);
    dispose();
  });

  it('non-freeform option: still forwards every key per-press (no local buffering)', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    seedFreeformChoicePrompt(store, { selected: 1 }); // cursor on a normal option
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    stdin.write('x');
    await tick();
    expect(inputStores.chatInput.getState().text).toBe('');
    const keys = submitsOfKind(fake, 'agent.send_key').map(
      (c) => (c.params as { payload: { key: string; literal: boolean } }).payload,
    );
    expect(keys).toEqual([{ agent_id: 'collab-1', key: 'x', literal: true, enter: false }]);
    dispose();
  });

  it('does NOT take over for an answered (finalized) prompt', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    seedChoicePrompt(store);
    store.setState((state) => {
      const blocks = state.conversations.transcripts['collab-1'] ?? [];
      const last = blocks[blocks.length - 1];
      if (last === undefined) return state;
      return {
        conversations: {
          ...state.conversations,
          transcripts: {
            ...state.conversations.transcripts,
            'collab-1': [{ ...last, raw: { ...last.raw, answered: true, chosen: 1 } }],
          },
        },
      };
    });
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    expect(lastFrame() ?? '').toContain('type a message');
    stdin.write('hi');
    await tick();
    expect(inputStores.chatInput.getState().text).toBe('hi');
    expect(submitsOfKind(fake, 'agent.send_key').length).toBe(0);
    dispose();
  });
});

describe('ChatInput — queued-message line', () => {
  it('renders the queued message one-line above the input with the send-now hint', async () => {
    const { store, inputStores, dispose } = await setup();
    seedQueued(store, 'please also check the tests\nand lint');
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain('⏸ queued · please also check the tests and lint');
    expect(frame).toContain('interrupt & send now');
    // The parser's working state rides the border title.
    expect(frame).toContain('· working');
    dispose();
  });

  it('enter on an empty buffer interrupts the agent (send now)', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    seedQueued(store, 'held message');
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    stdin.write(RETURN);
    await tick();
    const interrupts = submitsOfKind(fake, 'agent.interrupt');
    expect(interrupts.length).toBe(1);
    expect((interrupts[0]?.params as { payload: unknown }).payload).toMatchObject({
      agent_id: 'collab-1',
    });
    expect(submitsOfKind(fake, 'agent.message').length).toBe(0);
    dispose();
  });

  it('enter with text still sends the message normally (appends server-side)', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    seedQueued(store, 'held message');
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    stdin.write('more');
    await tick();
    stdin.write(RETURN);
    await tick();
    expect(submitsOfKind(fake, 'agent.interrupt').length).toBe(0);
    expect(submitsOfKind(fake, 'agent.message').length).toBe(1);
    dispose();
  });

  it('applyState updates the meta map from a conversation.state event', async () => {
    const { store, dispose } = await setup();
    store.getState().actions.conversations.applyState({
      type: 'conversation.state',
      event_id: 1,
      ts: 'now',
      run_id: 'r',
      agent_id: 'collab-1',
      conversation_id: 'collab-1',
      live_state: 'awaiting_input',
      queued_message: null,
    } as never);
    expect(store.getState().conversations.meta['collab-1']).toEqual({
      liveState: 'awaiting_input',
      queuedMessage: null,
    });
    dispose();
  });
});
