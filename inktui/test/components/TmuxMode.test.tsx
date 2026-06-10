/**
 * TmuxMode test — follows the ConfirmModal.test.tsx idiom (the C7M reference transient-mode test).
 * The recipe is the same: build stores, render the Overlay inside providers, drive with simulated
 * keys, assert frame content and lifecycle.
 *
 * Extra concerns for TmuxMode beyond ConfirmModal:
 *  1. **Subscription lifecycle** — the `tmux.frame` subscription MUST be open while the mode is
 *     active and MUST be closed (subscriberCount drops to 0) after exit, on BOTH exit paths.
 *  2. **Frame re-render** — `FakeBusClient.emit` drives a second frame and we assert it replaces
 *     the waiting placeholder.
 *  3. **alt+y exits** (not just Escape) — the headline feature; tests that `passThrough: true`
 *     actually works end-to-end (the bug class this catches: passThrough missing → alt+y swallowed
 *     by layer 0 → mode never exits on the second press).
 *  4. **Fullscreen suppresses layout** — while the mode is active, bars/panels are not rendered
 *     (the shell's `presentationHidesLayout` path).
 *  5. **Pure dispatch test** — alt+y maps to `toggleTmux` (a unit test over the dispatcher, no
 *     rendering needed).
 */

import { render } from 'ink-testing-library';
import type { JSX } from 'react';
import { describe, expect, it } from 'vitest';
import { FakeBusClient } from '../../src/bus/FakeBusClient.js';
import { Overlay } from '../../src/components/Overlay.js';
import { TMUX_MODE_ID, tmuxMode } from '../../src/components/TmuxMode.js';
import { BusClientProvider } from '../../src/hooks/useBusClient.js';
import { InputStoresProvider } from '../../src/hooks/useInputStores.js';
import { useRootInput } from '../../src/hooks/useRootInput.js';
import { createInputStores } from '../../src/input/createInputStores.js';
import { dispatchKey } from '../../src/input/dispatcher.js';
import { selectActiveMode } from '../../src/input/modeStore.js';
import { makeKey } from '../input/key.js';

const ALT_Y = '\x1by'; // alt+y — ESC + 'y', which Ink parses as { input: 'y', meta: true }

/** Let Ink flush a render + post-render effects. */
async function tick(): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, 20));
}

/** Runs the root input loop inside the providers (so simulated keys go through the real dispatcher). */
function RootInput(): null {
  useRootInput();
  return null;
}

/**
 * The harness: Overlay + root input loop inside the input stores + bus-client providers.
 * Mirrors the ConfirmModal harness; adds the BusClientProvider so TmuxFrame can open its
 * subscription.
 */
function Harness({
  stores,
  bus,
}: {
  readonly stores: ReturnType<typeof createInputStores>;
  readonly bus: FakeBusClient;
}): JSX.Element {
  return (
    <InputStoresProvider value={stores}>
      <BusClientProvider value={bus}>
        <RootInput />
        <Overlay />
      </BusClientProvider>
    </InputStoresProvider>
  );
}

/** Build stores with the tickets panel focused (focus to restore) + a fresh FakeBusClient. */
function setup() {
  const bus = new FakeBusClient();
  const stores = createInputStores(['tickets'], 'tickets');
  const enter = () => stores.modes.getState().enter(tmuxMode(stores.modes));
  return { bus, stores, enter };
}

describe('TmuxMode — alt+y fullscreen tmux mode', () => {
  it('enters, renders waiting placeholder, receives a frame, then exits via Escape and closes subscription', async () => {
    const { bus, stores, enter } = setup();
    const { lastFrame, stdin } = render(<Harness stores={stores} bus={bus} />);
    await tick();
    expect(lastFrame()).not.toContain('waiting'); // nothing up yet

    enter();
    await tick();
    expect(lastFrame()).toContain('waiting'); // waiting placeholder before first frame
    expect(selectActiveMode(stores.modes)?.id).toBe(TMUX_MODE_ID);
    expect(bus.subscriberCount).toBe(1); // subscription opened on enter

    // FakeBusClient drives a frame event
    bus.emit({
      type: 'tmux.frame',
      frame: 'HELLO_ANSI_FRAME',
      id: 'ev1',
      ts: '2026-01-01T00:00:00Z',
      run_id: 'r1',
      agent_id: 'a1',
    });
    await tick();
    expect(lastFrame()).toContain('HELLO_ANSI_FRAME'); // frame rendered

    // A second frame replaces the first
    bus.emit({
      type: 'tmux.frame',
      frame: 'SECOND_FRAME',
      id: 'ev2',
      ts: '2026-01-01T00:00:01Z',
      run_id: 'r1',
      agent_id: 'a1',
    });
    await tick();
    expect(lastFrame()).toContain('SECOND_FRAME');
    expect(lastFrame()).not.toContain('HELLO_ANSI_FRAME');

    // Escape dismisses: subscription closes, prior focus restored
    stdin.write('\x1b');
    await tick();
    expect(selectActiveMode(stores.modes)).toBeNull(); // mode dismissed
    expect(lastFrame()).not.toContain('SECOND_FRAME'); // overlay gone
    expect(bus.subscriberCount).toBe(0); // subscription closed — no leak
    expect(stores.focus.getState().intendedId).toBe('tickets'); // prior focus restored
  });

  it('alt+y again exits the mode (toggle off) and closes the subscription', async () => {
    const { bus, stores } = setup();
    const { stdin } = render(<Harness stores={stores} bus={bus} />);
    await tick();

    // alt+y enters the mode (via the default toggleTmux handler in useRootInput)
    stdin.write(ALT_Y);
    await tick();
    expect(selectActiveMode(stores.modes)?.id).toBe(TMUX_MODE_ID);
    expect(bus.subscriberCount).toBe(1); // subscription open

    // alt+y again exits (passThrough lets it reach the global chord layer → toggleTmux → exit)
    stdin.write(ALT_Y);
    await tick();
    expect(selectActiveMode(stores.modes)).toBeNull(); // mode exited
    expect(bus.subscriberCount).toBe(0); // subscription closed — no leak
    expect(stores.focus.getState().intendedId).toBe('tickets'); // prior focus restored (was 'tickets')
  });

  it('fullscreen mode: while active the overlay owns the whole screen (presentationHidesLayout)', async () => {
    const { bus, stores, enter } = setup();
    const { lastFrame } = render(<Harness stores={stores} bus={bus} />);
    await tick();

    // Before mode entry: nothing rendered (Overlay is empty in the harness-only render)
    expect(lastFrame()).not.toContain('waiting');

    enter();
    await tick();
    // The Overlay renders the TmuxFrame surface. The Shell's layout-hide logic (presentationHidesLayout)
    // is exercised at the App level; here we confirm the Overlay itself renders the fullscreen surface.
    expect(lastFrame()).toContain('waiting');
    expect(selectActiveMode(stores.modes)?.presentation).toBe('fullscreen');
  });

  it('no subscription leak: subscriberCount is 0 before and after the mode', async () => {
    const { bus, stores, enter } = setup();
    render(<Harness stores={stores} bus={bus} />);
    await tick();

    expect(bus.subscriberCount).toBe(0); // no subscription before entering
    enter();
    await tick();
    expect(bus.subscriberCount).toBe(1); // one subscription while active
    stores.modes.getState().exit(TMUX_MODE_ID);
    await tick();
    expect(bus.subscriberCount).toBe(0); // closed on exit — confirmed no leak
  });

  it('captures exclusively while active: non-tmux key is not routed to panel keymap (mode swallows)', async () => {
    // The mode uses passThrough:true, so keys NOT in the mode's keymap fall through to global chords
    // only. But the panel keymap is NOT a global chord, so a panel intent should NOT fire.
    const { bus, stores, enter } = setup();
    const { stdin } = render(<Harness stores={stores} bus={bus} />);
    await tick();
    enter();
    await tick();

    // alt+space would normally focus chat; with passThrough:true it still falls through to layer 1
    // (global chords) which fires focusChat. So alt+space DOES work while tmux is up (by design: passThrough).
    // But a plain panel key like 'j' (not a global chord) does NOT fire since panel keymaps are layer 3.
    const beforeFocus = stores.focus.getState().intendedId;
    stdin.write('j'); // panel key — should be swallowed by mode (no passThrough to panel layer)
    await tick();
    // Mode still active, focus unchanged from the saved context
    expect(selectActiveMode(stores.modes)?.id).toBe(TMUX_MODE_ID);
    expect(stores.focus.getState().intendedId).toBe(beforeFocus);
    void bus; // satisfy linter
  });
});

describe('alt+y dispatch — pure unit test (no rendering)', () => {
  it('alt+y fires toggleTmux in the global-chord layer', () => {
    let toggleCalled = false;
    const ctx = {
      focusedId: 'chat' as const,
      panelKeymaps: {},
      handlers: {
        focusPanel: () => {},
        navigate: () => {},
        focusChat: () => {},
        spawn: () => {},
        toggleTmux: () => {
          toggleCalled = true;
        },
        newPlan: () => {},
        newTicket: () => {},
        openSettings: () => {},
      },
      activeMode: null,
    };
    const outcome = dispatchKey('y', makeKey({ meta: true }), ctx);
    expect(outcome).toEqual({ layer: 'global', handled: true });
    expect(toggleCalled).toBe(true);
  });
});
