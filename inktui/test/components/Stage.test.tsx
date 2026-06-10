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
import { Stage } from '../../src/components/Stage.js';
import { AppStoreProvider } from '../../src/hooks/useAppStore.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { selectEffectiveFocus } from '../../src/input/focusStore.js';
import type { CrowSnapshotReply } from '../../src/store/roster/rosterActions.js';
import { createAppStore } from '../../src/store/store.js';

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

async function setup(reply: CrowSnapshotReply = oneCollaborator()) {
  const fake = new FakeBusClient();
  fake.stubRpc('state.crow_snapshot', reply);
  const { store, dispose } = createAppStore(fake);
  await store.getState().actions.roster.refresh();
  // plans visible + focused so directional nav has a left source; the Stage pane is the right target.
  const inputStores = createInputStores(['plans'], 'plans');
  return { fake, store, dispose, inputStores };
}

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

    // Focus the pane → it registers its j/k history-scroll keymap (the dispatcher routes j/k to it).
    stdin.write(ALT_L);
    await tick();
    const chords = (inputStores.keymaps.getState().keymaps[STAGE_PANE]?.keymap ?? []).map(
      (entry) => entry.chord.input,
    );
    expect(chords).toEqual(['k', 'j']);
    dispose();
  });

  it('scrolls the history window: newest turns by default, k reveals older turns', async () => {
    const { fake, store, inputStores, dispose } = await setup();
    // Seed 25 turns (> WINDOW of 20) so there is something above the default window to scroll into.
    emitTurns(fake, 25);
    void store; // store already wired to the same fake; emit feeds it via subscribe.
    const { stdin, lastFrame } = render(<Harness store={store} inputStores={inputStores} />);
    await tick();

    // Default (scroll 0): the window is pinned to the newest turns — the last is shown, the first is
    // scrolled off above (a `…` indicator marks more-above).
    const initial = lastFrame() ?? '';
    expect(initial).toContain('msg-24'); // newest visible
    expect(initial).not.toContain('msg-00'); // oldest scrolled off the top
    expect(initial).toContain('…'); // more-above indicator

    // Focus the pane (alt+l), then press `k` five times to scroll the window up toward older turns.
    stdin.write(ALT_L);
    await tick();
    for (let i = 0; i < 5; i++) {
      stdin.write('k');
    }
    await tick();

    // The window shifted up: the oldest turn is now visible, and the newest scrolled off the bottom.
    const scrolled = lastFrame() ?? '';
    expect(scrolled).toContain('msg-00'); // oldest now in view (5 up from a 25-turn, 20-window tail)
    expect(scrolled).not.toContain('msg-24'); // newest scrolled off the bottom
    dispose();
  });
});
