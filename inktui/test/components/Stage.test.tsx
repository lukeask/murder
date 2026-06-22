/**
 * Stage test (Phase 4a) — the center region's chat-history panes as focusable Stage panes.
 *
 * The three must-have behaviours from the phase contract:
 *  1. a favorited crow's chat pane mounts a Stage-pane rect (`stage:chat:<agentId>`) so it is a live
 *     directional-focus candidate;
 *  2. `alt+l` (directional nav right) from a left panel reaches that Stage pane — the geometry kernel
 *     scores over the real measured rects, the production hjkl path;
 *  3. focus re-homes to chat when the pane unmounts (its crow leaves the roster → `unmeasure`).
 *
 * Rendered in a real left-panel-beside-Stage row so the rects have a genuine left/right relationship
 * (the pure-geometry unit coverage lives in focusStore.test.ts; this proves the component wiring).
 */

import { Box } from 'ink';
import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import type { ConversationBlockEvent } from '../../src/bus/protocol.js';
import { PlansPanel } from '../../src/components/PlansPanel.js';
import {
  ChatPane,
  flattenTurns,
  formatTurnLines,
  paneContentHeights,
  Stage,
} from '../../src/components/Stage.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { BusClientProvider } from '../../src/hooks/useBusClient.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { CHAT_FOCUS, selectEffectiveFocus } from '../../src/input/focusStore.js';
import type { ChatTurn } from '../../src/selectors/conversationsSelectors.js';
import type { CrowSnapshotReply } from '../../src/store/roster/rosterActions.js';
import { createAppStore } from '../../src/store/store.js';
import { inkTestColorOn } from '../inkTestColorOn.js';

const ALT_L = '\x1bl'; // alt+l → directional nav right (alt-prefixed, terminal-representable)

/** Let Ink flush a render + the post-layout measure/keymap effects. */
async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 30));
}

/** A roster with one default-favorited collaborator → exactly one Stage chat pane. */
function oneCollaborator(): CrowSnapshotReply {
  return {
    invalidation_key: 'iv',
    sessions: [
      { agent_id: 'collab-1', role: 'collaborator', status: 'idle', session_name: 'TestCollab' },
    ],
  };
}

/** An empty roster → no favorited crows → no Stage chat panes. */
function emptyRoster(): CrowSnapshotReply {
  return { invalidation_key: 'iv2', sessions: [] };
}

/** Local harness: a left panel beside the Stage, both inside the providers + the one root input loop
 * (the production path). The `plans` panel is seeded visible + focused so `alt+l` can navigate right
 * from it into the Stage. */
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
        <Box flexDirection="row" width={80} height={30}>
          <Box width={30}>
            <PlansPanel />
          </Box>
          <Stage />
        </Box>
      </InputStoresProvider>
    </AppStoreProvider>
  );
}

function RootInput(): null {
  useRootInput();
  return null;
}

const STAGE_PANE = 'stage:chat:collab-1';

/** Emit `n` distinct user blocks for `collab-1` (each a unique id so they push, not replace) so the
 * pane has more turns than its WINDOW (20) — exercising the scroll-window slice math + j/k. */
function emitTurns(fake: FakeBusClient, n: number): void {
  for (let i = 0; i < n; i++) {
    const label = `msg-${String(i).padStart(2, '0')}`;
    const event: ConversationBlockEvent = {
      type: 'conversation.block',
      id: `ev-${label}`,
      ts: '2026-06-08T00:00:00Z',
      run_id: 'run-1',
      agent_id: 'collab-1',
      conversation_id: 'conv-collab-1',
      action: 'block-appended',
      block: { type: 'user', id: `block-${label}`, text: label },
    };
    fake.emit(event);
  }
}

describe('formatTurnLines (TUIchat-2: block-classified physical lines, no inline ›/· prefix)', () => {
  it('emits a multi-line prose block verbatim (faithful newlines), first line flagged firstOfTurn', () => {
    const turn: ChatTurn = { blockId: 'b1', speaker: 'assistant', text: 'first\nsecond' };
    expect(formatTurnLines(turn)).toEqual([
      { speaker: 'assistant', kind: 'prose', text: 'first', firstOfTurn: true },
      { speaker: 'assistant', kind: 'prose', text: 'second', firstOfTurn: false },
    ]);
  });

  it('drops the inline marker — a user turn is plain text in a prose block', () => {
    const turn: ChatTurn = { blockId: 'b2', speaker: 'user', text: 'hello' };
    expect(formatTurnLines(turn)).toEqual([
      { speaker: 'user', kind: 'prose', text: 'hello', firstOfTurn: true },
    ]);
  });

  it('separates blocks with exactly one blank line and labels code/list kinds', () => {
    const turn: ChatTurn = {
      blockId: 'b3',
      speaker: 'assistant',
      text: 'intro\n\n```\ncode()\n```\n\n- item',
    };
    expect(formatTurnLines(turn)).toEqual([
      { speaker: 'assistant', kind: 'prose', text: 'intro', firstOfTurn: true },
      { speaker: 'assistant', kind: 'blank', text: '', firstOfTurn: false },
      { speaker: 'assistant', kind: 'code', text: 'code()', firstOfTurn: true },
      { speaker: 'assistant', kind: 'blank', text: '', firstOfTurn: false },
      { speaker: 'assistant', kind: 'list', text: '- item', firstOfTurn: true },
    ]);
  });

  it('returns no lines for an empty turn', () => {
    const turn: ChatTurn = { blockId: 'b4', speaker: 'assistant', text: '' };
    expect(formatTurnLines(turn)).toEqual([]);
  });
});

describe('flattenTurns', () => {
  it('separates consecutive turns with one blank line (a real ChatLine, so scroll math counts it)', () => {
    const turns: ChatTurn[] = [
      { blockId: 'b1', speaker: 'assistant', text: 'reply' },
      { blockId: 'b2', speaker: 'user', text: 'question' },
    ];
    expect(flattenTurns(turns)).toEqual([
      { speaker: 'assistant', kind: 'prose', text: 'reply', firstOfTurn: true },
      { speaker: 'user', kind: 'blank', text: '', firstOfTurn: false },
      { speaker: 'user', kind: 'prose', text: 'question', firstOfTurn: true },
    ]);
  });

  it('adds no separator around a single turn (no leading/trailing blank)', () => {
    const turns: ChatTurn[] = [{ blockId: 'b1', speaker: 'assistant', text: 'one\ntwo' }];
    expect(flattenTurns(turns)).toEqual([
      { speaker: 'assistant', kind: 'prose', text: 'one', firstOfTurn: true },
      { speaker: 'assistant', kind: 'prose', text: 'two', firstOfTurn: false },
    ]);
  });

  it('skips an empty turn entirely (no stray separator)', () => {
    const turns: ChatTurn[] = [
      { blockId: 'b1', speaker: 'assistant', text: 'kept' },
      { blockId: 'b2', speaker: 'user', text: '' },
    ];
    expect(flattenTurns(turns)).toEqual([
      { speaker: 'assistant', kind: 'prose', text: 'kept', firstOfTurn: true },
    ]);
  });
});

async function setup(reply: CrowSnapshotReply = oneCollaborator()) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.crow_snapshot', reply);
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.roster.refresh();
  // plans visible + focused so directional nav has a left source; the Stage pane is the right target.
  const inputStores = createInputStores(['plans'], 'plans');
  return { fake, store, dispose, inputStores };
}

describe('paneContentHeights', () => {
  it('splits an even grid height evenly and subtracts the 2-row chrome', () => {
    expect(paneContentHeights(30, 2, 0)).toEqual([13, 13]);
  });

  it('spreads the remainder onto the first rows (one extra cell)', () => {
    expect(paneContentHeights(31, 2, 0)).toEqual([14, 13]);
  });

  it('subtracts the inter-row gaps before distributing', () => {
    // gaps = 2 → avail = 28 → base = 14 → 14 − 2 chrome = 12 per row.
    expect(paneContentHeights(30, 2, 2)).toEqual([12, 12]);
  });

  it('handles a single row', () => {
    expect(paneContentHeights(10, 1, 0)).toEqual([8]);
  });

  it('returns undefined per row before the grid has measured (height 0)', () => {
    expect(paneContentHeights(0, 2, 0)).toEqual([undefined, undefined]);
  });

  it('returns [] for no rows', () => {
    expect(paneContentHeights(5, 0, 0)).toEqual([]);
  });
});

describe('ChatPane — window honors the contentHeight prop', () => {
  it('bounds the visible window to the contentHeight prop (deterministic, no self-measure)', async () => {
    // Previously impossible to test: ink-testing-library renders sizeless, so `measureElement`
    // returned 0 and the window was always FALLBACK_HEIGHT. Now the height is a prop, so a small
    // contentHeight provably clamps the window regardless of the (zero) measured render size.
    const { fake, store, inputStores, dispose } = await setup();
    emitTurns(fake, 30); // ~59 flattened lines (a blank separator between turns) — far above 5
    const identity = { kind: 'collaborator' as const, agentId: 'collab-1', label: 'TestCollab' };
    const { lastFrame } = render(
      <AppStoreProvider value={store}>
        <InputStoresProvider value={inputStores}>
          <RootInput />
          <Box flexDirection="column" width={80} height={30}>
            <ChatPane
              identity={identity}
              conversations={store.getState().conversations}
              chatTarget={false}
              footer={null}
              worktree={null}
              contentHeight={5}
            />
          </Box>
        </InputStoresProvider>
      </AppStoreProvider>,
    );
    await tick();

    const frame = lastFrame() ?? '';
    // The newest content line is present (window pinned to the tail) ...
    expect(frame).toContain('msg-29');
    // ... and an early/oldest line is NOT — the window is bounded to ~5 lines, not FALLBACK (20).
    expect(frame).not.toContain('msg-00');
    dispose();
  });
});

describe('Stage — empty-Stage first-run hint', () => {
  it('shows the spawn/star hint instead of a void when no panes and no doc are open', async () => {
    const { store, inputStores, dispose } = await setup(emptyRoster());
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    const frame = lastFrame() ?? '';
    // Labels come from the live bindings (default modifier alt → A-s spawn, A-f star).
    expect(frame).toContain('A-s spawn a crow');
    expect(frame).toContain('A-f star one in the crows panel');
    dispose();
  });
});

describe('Stage — chat-history panes as focusable Stage panes', () => {
  it('mounts a Stage-pane rect for a favorited crow and titles the pane', async () => {
    const { store, inputStores, dispose } = await setup();
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // The pane is titled for the collaborator (proves it rendered) ...
    expect(lastFrame() ?? '').toContain('TestCollab');
    // ... and it registered a measured rect under its Stage-pane focus id (a live nav candidate).
    expect(inputStores.focus.getState().rects.has(STAGE_PANE)).toBe(true);
    dispose();
  });

  it('alt+l from the left panel reaches the Stage chat pane (hjkl directional nav)', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Source focus is the left plans panel.
    expect(selectEffectiveFocus(inputStores.focus)).toBe('plans');
    // Navigate right → the geometry kernel scores over the real rects and lands on the Stage pane.
    stdin.write(ALT_L);
    await tick();
    expect(selectEffectiveFocus(inputStores.focus)).toBe(STAGE_PANE);
    dispose();
  });

  it('re-homes focus to chat when the focused chat pane unmounts (crow leaves the roster)', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    const { stdin, rerender } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Focus the Stage pane via directional nav.
    stdin.write(ALT_L);
    await tick();
    expect(selectEffectiveFocus(inputStores.focus)).toBe(STAGE_PANE);

    // The crow leaves: re-stub the snapshot to empty + refresh the roster → the pane unmounts → its
    // measure-effect cleanup drops the rect (unmeasure) → the derived invariant re-homes focus to
    // chat. No imperative re-home call — it falls out of resolveFocus.
    fake.stubRpc('state.crow_snapshot', emptyRoster());
    await store.getState().actions.roster.refresh();
    rerender(<Harness store={store} inputStores={inputStores} />);
    await tick();

    expect(inputStores.focus.getState().rects.has(STAGE_PANE)).toBe(false);
    expect(selectEffectiveFocus(inputStores.focus)).toBe('chat');
    dispose();
  });

  it('a focused chat pane declares its history-scroll keymap (j/k) to the registry', async () => {
    const { store, inputStores, dispose } = await setup();
    const { stdin } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Blurred (plans focused): the pane registers an EMPTY keymap, so it claims no chord.
    expect(inputStores.keymaps.getState().keymaps[STAGE_PANE]?.keymap ?? []).toEqual([]);

    // Focus the pane → it registers its keymap (the dispatcher routes the chords to it). The
    // VISIBLE chords: the go-to-line `g` (the shared gesture) leads, then the j/k history-scroll
    // pair; the gesture's hidden digit sub-steps ride along but are not hints.
    stdin.write(ALT_L);
    await tick();
    const entries = inputStores.keymaps.getState().keymaps[STAGE_PANE]?.keymap ?? [];
    const visible = entries
      .filter((entry) => entry.hidden !== true)
      // These entries use single chords (not the list form); narrow for the assertion.
      .map((entry) => (Array.isArray(entry.chord) ? entry.chord[0] : entry.chord).input);
    expect(visible).toEqual(['g', 'k', 'j']);
    // The pre-registered digits are present (so a same-chunk `g3` lands) but hidden.
    const hiddenInputs = entries
      .filter((entry) => entry.hidden === true)
      .map((entry) => (Array.isArray(entry.chord) ? entry.chord[0] : entry.chord).input);
    expect(hiddenInputs).toContain('0');
    dispose();
  });

  it('soft-wraps a long turn instead of truncating with an ellipsis', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    const long = 'word '.repeat(30).trim();
    const event: ConversationBlockEvent = {
      type: 'conversation.block',
      id: 'ev-long',
      ts: '2026-06-08T00:00:00Z',
      run_id: 'run-1',
      agent_id: 'collab-1',
      conversation_id: 'conv-collab-1',
      action: 'block-appended',
      block: { type: 'assistant', id: 'block-long', text: long },
    };
    fake.emit(event);
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();
    const frame = lastFrame() ?? '';
    expect(frame).toContain(long.slice(0, 20));
    expect(frame).not.toContain('…');
    dispose();
  });

  it('scrolls a SINGLE long multi-line turn (line-based window, not turn-based)', async () => {
    // The regression: one tall turn (50 lines) used to leave maxScrollUp = turns.length(1) − height ≤ 0,
    // so k/j were dead and the top of a long message was unreachable. Line-based windowing fixes it.
    const { fake, store, inputStores, dispose } = await setup();
    const body = Array.from({ length: 50 }, (_, i) => `line-${String(i).padStart(2, '0')}`).join(
      '\n',
    );
    const event: ConversationBlockEvent = {
      type: 'conversation.block',
      id: 'ev-tall',
      ts: '2026-06-08T00:00:00Z',
      run_id: 'run-1',
      agent_id: 'collab-1',
      conversation_id: 'conv-collab-1',
      action: 'block-appended',
      block: { type: 'assistant', id: 'block-tall', text: body },
    };
    fake.emit(event);
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Pinned to the newest lines by default: the tail shows, the head is scrolled off above.
    const initial = lastFrame() ?? '';
    expect(initial).toContain('line-49');
    expect(initial).not.toContain('line-00');

    // Focus the pane and scroll up to saturation → the head of the message becomes reachable.
    stdin.write(ALT_L);
    await tick();
    for (let i = 0; i < 60; i++) {
      stdin.write('k');
    }
    await tick();
    const scrolled = lastFrame() ?? '';
    expect(scrolled).toContain('line-00');
    expect(scrolled).not.toContain('line-49');
    dispose();
  });

  it('scrolls the history window: newest turns by default, k reveals older turns', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    // Seed 50 turns (well above any measured window height) so there is always content above the
    // default window to scroll into, regardless of the exact measured height the test harness reports.
    emitTurns(fake, 50);
    void store; // store already wired to the same fake; emit feeds it via subscribe.
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Default (scroll 0): the window is pinned to the newest turns — the last is shown, the first is
    // scrolled off above. The scrollbar column (not a `…` marker) now communicates position.
    const initial = lastFrame() ?? '';
    expect(initial).toContain('msg-49'); // newest visible
    expect(initial).not.toContain('msg-00'); // oldest scrolled off the top

    // Focus the pane (alt+l), then press `k` many times to saturate scrollUp at maxScrollUp, ensuring
    // msg-00 is in view regardless of the exact measured window height. 50 turns flatten to ~99
    // physical lines (one blank separator between turns), so saturation needs >99 presses.
    stdin.write(ALT_L);
    await tick();
    for (let i = 0; i < 120; i++) {
      stdin.write('k');
    }
    await tick();

    // The window shifted to the top: the oldest turn is now visible, the newest scrolled off the bottom.
    const scrolled = lastFrame() ?? '';
    expect(scrolled).toContain('msg-00'); // oldest now in view
    expect(scrolled).not.toContain('msg-49'); // newest scrolled off the bottom
    dispose();
  });

  it('scrolls via the wheel bus WITHOUT focusing the pane (the chat-input target case)', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    emitTurns(fake, 50);
    void store;
    const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // The pane is NOT focused (focus stays on the seeded 'plans' panel) — exactly the state when the
    // user is typing in the chat input. A wheel notch routed to this pane's focus id must still scroll
    // it, proving the bus subscription is independent of focus (unlike the j/k keymap).
    expect(selectEffectiveFocus(inputStores.focus)).toBe('plans');
    const initial = lastFrame() ?? '';
    expect(initial).toContain('msg-49');
    expect(initial).not.toContain('msg-00');

    for (let i = 0; i < 120; i++) {
      inputStores.paneScroll.emit(STAGE_PANE, 'up', 1);
    }
    await tick();

    const scrolled = lastFrame() ?? '';
    expect(scrolled).toContain('msg-00');
    expect(scrolled).not.toContain('msg-49');
    dispose();
  });
});

describe('Stage — chat-target highlight', () => {
  // ink-testing-library omits ANSI unless chalk level ≥ 3 (FORCE_COLOR=3); the highlight is purely a
  // color/bold flip on the pane chrome, so the visual case runs only then (the Ledger convention).
  const colorOn = inkTestColorOn();

  it.skipIf(!colorOn)(
    'highlights the chat-target pane while the chat input holds focus',
    async () => {
      const { store, inputStores, dispose } = await setup();
      const { lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
      await tick();

      // The raw (ANSI-carrying) chrome around the pane title — the only thing the highlight changes.
      const segment = (frame: string): string => {
        const idx = frame.indexOf('TestCollab');
        return frame.slice(Math.max(idx - 40, 0), idx + 40);
      };
      // Plans focused: the pane neither holds focus nor is the chat input focused → blurred chrome.
      expect(selectEffectiveFocus(inputStores.focus)).toBe('plans');
      const blurred = segment(lastFrame() ?? '');

      // Focus the chat input. The collaborator is the active send target (the only open pane), so its
      // pane lights up even though the effective focus is the chat input, not the pane itself.
      inputStores.focus.getState().focus(CHAT_FOCUS);
      await tick();
      const targeted = segment(lastFrame() ?? '');
      expect(targeted).not.toBe(blurred);
      dispose();
    },
  );

  it('a target-highlighted pane does NOT claim j/k — its keymap stays gated on the real focus', async () => {
    const { store, inputStores, dispose } = await setup();
    render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Chat focused → the pane is target-highlighted, but the registry must hold its EMPTY keymap
    // (a highlighted-but-unfocused pane stealing `j`/`k` would eat typed characters).
    inputStores.focus.getState().focus(CHAT_FOCUS);
    await tick();
    expect(selectEffectiveFocus(inputStores.focus)).toBe(CHAT_FOCUS);
    expect(inputStores.keymaps.getState().keymaps[STAGE_PANE]?.keymap ?? []).toEqual([]);
    dispose();
  });
});

/** A tmux.frame event for `collab-1` (the pane-scoped raw capture). */
function tmuxFrame(frame: string, id: string) {
  return {
    type: 'tmux.frame' as const,
    frame,
    id,
    ts: '2026-06-22T00:00:00Z',
    run_id: 'run-1',
    agent_id: 'collab-1',
  };
}

describe('Stage — inline tmux view (TUIchat-5)', () => {
  /** The Harness plus the BusClientProvider the inline frame needs (Stage reads the live store, so
   * flipping the pane's view mode via the action re-renders it into / out of the tmux branch). */
  function TmuxHarness({
    store,
    inputStores,
    bus,
  }: {
    readonly store: ReturnType<typeof createAppStore>['store'];
    readonly inputStores: ReturnType<typeof createInputStores>;
    readonly bus: FakeBusClient;
  }): JSX.Element {
    return (
      <BusClientProvider value={bus}>
        <Harness store={store} inputStores={inputStores} />
      </BusClientProvider>
    );
  }

  it('renders the inline frame when the pane is in tmux mode and closes the stream on mode-leave', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    const { lastFrame, rerender } = render(
      <TmuxHarness store={store} inputStores={inputStores} bus={fake} />,
    );
    await tick();

    // Baseline: the store itself holds some subscriptions on the shared fake bus (conversation.block,
    // snapshots, …). We assert the DELTA from the inline frame, not an absolute count. Verbose by
    // default → the inline frame surface is NOT mounted, so it adds nothing.
    const baseline = fake.subscriberCount;

    // Flip the pane to tmux mode → the inline frame mounts and opens exactly one pane-scoped
    // subscription; before the first frame it shows the waiting placeholder.
    store.getState().actions.conversations.setPaneViewMode('collab-1', 'tmux');
    rerender(<TmuxHarness store={store} inputStores={inputStores} bus={fake} />);
    await tick();
    expect(fake.subscriberCount).toBe(baseline + 1);
    expect(lastFrame() ?? '').toContain('waiting for tmux frame');

    // A frame arrives → it renders inline inside the pane (no fullscreen takeover: the pane title is
    // still present).
    fake.emit(tmuxFrame('INLINE_TMUX_FRAME', 'ev-1'));
    await tick();
    const withFrame = lastFrame() ?? '';
    expect(withFrame).toContain('INLINE_TMUX_FRAME');
    expect(withFrame).toContain('TestCollab'); // pane chrome intact — inline, not a takeover

    // Leave tmux mode (back to verbose) → the inline frame unmounts → its subscription closes. This
    // is the "close on mode-leave, no idle streams" lifecycle (not only on pane destroy).
    store.getState().actions.conversations.setPaneViewMode('collab-1', 'verbose');
    rerender(<TmuxHarness store={store} inputStores={inputStores} bus={fake} />);
    await tick();
    expect(fake.subscriberCount).toBe(baseline);
    expect(lastFrame() ?? '').not.toContain('INLINE_TMUX_FRAME');
    dispose();
  });
});
