/**
 * modeStore tests — the transient-mode stack and its focus save/restore, framework-agnostic (no
 * React render needed; this is the store-logic idiom the C7M pattern is built on). Covers enter/exit,
 * stack push/pop, exit-by-id of a buried frame, idempotent re-enter, and that focus is saved on enter
 * and restored on the matching exit.
 */

import { describe, expect, it } from 'vitest';
import { createInputStores } from '../../src/input/createInputStores.js';
import { type Mode, type ModeStoreApi, selectActiveMode } from '../../src/input/modeStore.js';

/** A no-op render mode with the given id; presentation/keymap are irrelevant to the store logic. */
function mode(id: string): Mode {
  return {
    id,
    presentation: 'modal',
    keymap: [],
    onIntent: () => {},
    render: () => null,
  };
}

/** Build input stores with the panels visible and focus on `tickets`, plus the bound mode store. */
function setup(): { modes: ModeStoreApi; stores: ReturnType<typeof createInputStores> } {
  const stores = createInputStores(['tickets', 'crows'], 'tickets');
  return { modes: stores.modes, stores };
}

describe('modeStore — enter/exit', () => {
  it('starts empty (no active mode)', () => {
    const { modes } = setup();
    expect(modes.getState().stack).toHaveLength(0);
    expect(selectActiveMode(modes)).toBeNull();
  });

  it('enter pushes a mode as the active one', () => {
    const { modes } = setup();
    modes.getState().enter(mode('a'));
    expect(selectActiveMode(modes)?.id).toBe('a');
    expect(modes.getState().stack).toHaveLength(1);
  });

  it('exit() pops the top mode', () => {
    const { modes } = setup();
    modes.getState().enter(mode('a'));
    modes.getState().exit();
    expect(selectActiveMode(modes)).toBeNull();
  });

  it('exit() on an empty stack is a no-op', () => {
    const { modes } = setup();
    const before = modes.getState().stack;
    modes.getState().exit();
    expect(modes.getState().stack).toBe(before); // same identity — no churn
  });

  it('exit(id) for an absent id is a no-op', () => {
    const { modes } = setup();
    modes.getState().enter(mode('a'));
    const before = modes.getState().stack;
    modes.getState().exit('nope');
    expect(modes.getState().stack).toBe(before);
  });
});

describe('modeStore — stack semantics', () => {
  it('a mode opened over a mode pushes; the top is active', () => {
    const { modes } = setup();
    modes.getState().enter(mode('a'));
    modes.getState().enter(mode('b'));
    expect(modes.getState().stack.map((f) => f.mode.id)).toEqual(['a', 'b']);
    expect(selectActiveMode(modes)?.id).toBe('b');
  });

  it('exit() pops back to the underlying mode', () => {
    const { modes } = setup();
    modes.getState().enter(mode('a'));
    modes.getState().enter(mode('b'));
    modes.getState().exit();
    expect(selectActiveMode(modes)?.id).toBe('a');
  });

  it('exit(id) removes a buried frame, leaving the top active', () => {
    const { modes } = setup();
    modes.getState().enter(mode('a'));
    modes.getState().enter(mode('b'));
    modes.getState().exit('a'); // remove the buried one
    expect(modes.getState().stack.map((f) => f.mode.id)).toEqual(['b']);
    expect(selectActiveMode(modes)?.id).toBe('b');
  });

  it('re-entering an id already on the stack is idempotent (single instance, moved to top)', () => {
    const { modes } = setup();
    modes.getState().enter(mode('a'));
    modes.getState().enter(mode('b'));
    modes.getState().enter(mode('a')); // re-enter the buried 'a'
    expect(modes.getState().stack.map((f) => f.mode.id)).toEqual(['b', 'a']);
    expect(selectActiveMode(modes)?.id).toBe('a');
  });
});

describe('modeStore — focus save/restore', () => {
  it('saves focus on enter and restores it on exit', () => {
    const { modes, stores } = setup();
    expect(stores.focus.getState().intendedId).toBe('tickets');
    modes.getState().enter(mode('a'));
    // While the mode is up the consumer can move focus freely; exit must restore the entry focus.
    stores.focus.getState().focus('crows');
    modes.getState().exit();
    expect(stores.focus.getState().intendedId).toBe('tickets');
  });

  it('nested modes restore focus layer by layer (each frame remembers its own entry focus)', () => {
    const { modes, stores } = setup(); // focus starts on tickets
    modes.getState().enter(mode('a')); // saves tickets
    stores.focus.getState().focus('crows'); // user moves focus under 'a'
    modes.getState().enter(mode('b')); // saves crows
    modes.getState().exit(); // pop 'b' → restore crows
    expect(stores.focus.getState().intendedId).toBe('crows');
    modes.getState().exit(); // pop 'a' → restore tickets
    expect(stores.focus.getState().intendedId).toBe('tickets');
  });

  it('a re-entered mode keeps its ORIGINAL saved focus (does not save its own surface)', () => {
    const { modes, stores } = setup(); // focus starts on tickets
    modes.getState().enter(mode('a')); // saves tickets
    modes.getState().enter(mode('b')); // saves whatever is effective now (tickets)
    modes.getState().enter(mode('a')); // re-enter buried 'a' — must keep its original saved tickets
    modes.getState().exit(); // pop the re-entered 'a'
    expect(stores.focus.getState().intendedId).toBe('tickets');
  });

  it('exit(id) of a buried frame does NOT move live focus (only the top frame restores)', () => {
    const { modes, stores } = setup(); // focus tickets
    modes.getState().enter(mode('a')); // saves tickets
    stores.focus.getState().focus('crows');
    modes.getState().enter(mode('b')); // saves crows
    modes.getState().exit('a'); // remove buried 'a' — top 'b' still owns capture, focus unchanged
    expect(stores.focus.getState().intendedId).toBe('crows');
  });
});
