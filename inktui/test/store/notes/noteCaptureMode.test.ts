/**
 * `noteCaptureMode` dispatch tests — proves the note-capture ESC-chord FSM is expressible through the
 * **existing root dispatcher** with no new primitive (the F9 "verify before porting" verdict, now
 * demonstrated rather than asserted). Keys are synthesised and pushed through {@link dispatchKey} into
 * a real {@link createNoteCaptureStore} FSM, asserting the four transitions end-to-end:
 *  - ESC arms then (double-tap) commits → dismiss,
 *  - `d` is the delete chord ONLY while armed, an ordinary character otherwise (the gate),
 *  - `u` undoes only with a snapshot, an ordinary character otherwise,
 *  - the blur timer (driven by the store) fires the focus move under the real-timer idiom.
 *
 * The mode's keymap routes `escape`/`return` to `onIntent`; everything context-sensitive (`d`/`u`/
 * printable) goes through `onUncaptured` — exactly the C12 ticketEditor pattern. We drive the
 * dispatcher's layer-0 capture, so this exercises the real routing, not the store verbs directly.
 */

import { describe, expect, it, vi } from 'vitest';
import { dispatchKey, type GlobalHandlers } from '../../../src/input/dispatcher.js';
import { createFocusStore } from '../../../src/input/focusStore.js';
import { createModeStore, selectActiveMode } from '../../../src/input/modeStore.js';
import { createPanelStore } from '../../../src/input/panelStore.js';
import {
  type NoteCaptureModeOptions,
  noteCaptureMode,
} from '../../../src/store/notes/noteCaptureMode.js';
import {
  BLUR_DELAY_MS,
  createNoteCaptureStore,
  type NoteCaptureStoreApi,
} from '../../../src/store/notes/noteCaptureStore.js';
import { makeKey } from '../../input/key.js';

/** Wait `ms` real milliseconds — the blur self-fires on a real timer (toastStore idiom). */
function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** No-op global handlers — the capture mode captures everything, so these must never be hit. */
function noopHandlers(): GlobalHandlers {
  return {
    focusPanel: vi.fn(),
    navigate: vi.fn(),
    focusChat: vi.fn(),
    spawn: vi.fn(),
    toggleTmux: vi.fn(),
    newPlan: vi.fn(),
    newTicket: vi.fn(),
    openSettings: vi.fn(),
    keyHelp: vi.fn(),
    quickNote: vi.fn(),
    cycleTargetPrev: vi.fn(),
    cycleTargetNext: vi.fn(),
    toggleTargetPane: vi.fn(),
    murder: vi.fn(),
    murderPending: vi.fn(() => false),
    murderConfirm: vi.fn(),
    murderCancel: vi.fn(),
    closePane: vi.fn(),
  };
}

/** Stand up a mode store with the note-capture mode entered, plus the FSM store and option spies.
 * Returns everything a case needs to push keys and assert. */
function setup(opts?: Partial<NoteCaptureModeOptions>): {
  store: NoteCaptureStoreApi;
  onSubmit: ReturnType<typeof vi.fn>;
  onCancel: ReturnType<typeof vi.fn>;
  modes: ReturnType<typeof createModeStore>;
  press: (input: string, key?: Partial<Parameters<typeof makeKey>[0]>) => void;
} {
  const panels = createPanelStore();
  const focus = createFocusStore(panels);
  const modes = createModeStore(focus);
  const store = createNoteCaptureStore();
  const onSubmit = vi.fn(opts?.onSubmit);
  const onCancel = vi.fn(opts?.onCancel);
  modes.getState().enter(noteCaptureMode(modes, store, { onSubmit, onCancel }));

  const handlers = noopHandlers();
  function press(input: string, key: Partial<Parameters<typeof makeKey>[0]> = {}): void {
    // The active mode is read from the live store each press (it may change as the mode dismisses).
    const activeMode = selectActiveMode(modes);
    dispatchKey(input, makeKey(key), {
      focusedId: 'chat',
      handlers,
      panelKeymaps: {},
      activeMode,
    });
  }

  return { store, onSubmit, onCancel, modes, press };
}

describe('noteCaptureMode — ESC double-tap → dismiss', () => {
  it('a single ESC arms (mode stays up, no cancel)', () => {
    const { store, onCancel, modes, press } = setup();
    press('', { escape: true });
    expect(store.getState().escArmedAt).not.toBeNull();
    expect(store.getState().blurTimerActive).toBe(true);
    expect(onCancel).not.toHaveBeenCalled();
    expect(selectActiveMode(modes)).not.toBeNull(); // still captured
    store.getState().reset();
  });

  it('two quick ESCs commit → onCancel + mode dismissed; draft PERSISTS (item 10)', () => {
    const { store, onCancel, modes, press } = setup();
    store.getState().setDraft('half-written');
    press('', { escape: true });
    press('', { escape: true }); // immediate second press — well inside the 0.45s window
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(selectActiveMode(modes)).toBeNull(); // mode exited
    // Cancel does NOT reset the FSM — the draft survives for the next open (item 10).
    expect(store.getState().draftText).toBe('half-written');
    store.getState().reset();
  });
});

describe('noteCaptureMode — ESC-then-d delete chord (the gate)', () => {
  it('d AFTER esc clears the draft (delete chord)', () => {
    const { store, press } = setup();
    store.getState().setDraft('capture me');
    press('', { escape: true }); // arms → blur timer live
    press('d');
    expect(store.getState().draftText).toBe('');
    expect(store.getState().undoSnapshot).toBe('capture me');
    store.getState().reset();
  });

  it('d WITHOUT a prior esc is an ordinary character (gate closed)', () => {
    const { store, press } = setup();
    store.getState().setDraft('abc');
    press('d'); // not armed → literal
    expect(store.getState().draftText).toBe('abcd');
    expect(store.getState().undoSnapshot).toBeNull();
    store.getState().reset();
  });
});

describe('noteCaptureMode — undo', () => {
  it('u after a delete restores the draft; u with nothing to undo is a literal char', () => {
    const { store, press } = setup();
    store.getState().setDraft('keep this');
    press('', { escape: true });
    press('d'); // delete → snapshot taken, draft cleared
    press('u'); // undo → restore
    expect(store.getState().draftText).toBe('keep this');

    // A second u has no snapshot → it is an ordinary character.
    press('u');
    expect(store.getState().draftText).toBe('keep thisu');
    store.getState().reset();
  });
});

describe('noteCaptureMode — submit + plain entry', () => {
  it('Enter on a non-empty draft submits and dismisses; empty Enter does nothing', () => {
    const { store, onSubmit, modes, press } = setup();
    // Empty draft: Enter is a no-op.
    press('', { return: true });
    expect(onSubmit).not.toHaveBeenCalled();
    expect(selectActiveMode(modes)).not.toBeNull();

    store.getState().setDraft('a real note');
    press('', { return: true });
    expect(onSubmit).toHaveBeenCalledWith('a real note', undefined);
    expect(selectActiveMode(modes)).toBeNull();
    // Submit resets the FSM so a captured note never leaks into the next capture (item 10).
    expect(store.getState().draftText).toBe('');
  });

  it('ordinary characters append to the draft and never arm ESC', () => {
    const { store, press } = setup();
    press('h');
    press('i');
    expect(store.getState().draftText).toBe('hi');
    expect(store.getState().escArmedAt).toBeNull();
    store.getState().reset();
  });
});

describe('noteCaptureMode — blur timeout (store-driven)', () => {
  it('after the idle delay the store blurs focus draft→list', async () => {
    const { store, press } = setup();
    store.getState().setDraft('idle');
    press('', { escape: true });
    expect(store.getState().focus).toBe('draft');
    await wait(BLUR_DELAY_MS + 40);
    expect(store.getState().focus).toBe('list');
    expect(store.getState().blurTimerActive).toBe(false);
    store.getState().reset();
  });
});
