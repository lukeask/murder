/**
 * `noteCaptureStore` tests — the F9 port of Textual's `NoteCaptureScreen` ESC-chord FSM
 * (`app/tui/note_capture.py:109-247`). One case per FSM transition the plan names:
 *  - ESC double-tap commit (and the single-tap arm that precedes it),
 *  - the ESC-then-`d` delete chord,
 *  - the blur timeout (focus draft→list + disarm),
 *  - the single-level undo of a delete.
 *
 * Style mirrors `toastStore.test.ts`: a fresh factory instance per case (no shared global state, no
 * leaked timer), an injectable `now` for the deterministic double-tap window, and the repo's real-
 * timer + `wait()` idiom for the blur (no fake timers in this codebase).
 */

import { describe, expect, it } from 'vitest';
import {
  BLUR_DELAY_MS,
  createNoteCaptureStore,
  ESC_DOUBLE_TAP_MS,
} from '../../../src/store/notes/noteCaptureStore.js';

/** Wait `ms` real milliseconds — the blur self-fires on a real timer (no fake timers in this repo). */
function wait(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

describe('noteCaptureStore — ESC double-tap', () => {
  it('the first ESC arms and schedules the blur (stays open)', () => {
    const store = createNoteCaptureStore();
    const outcome = store.getState().pressEscape(1000);
    expect(outcome).toBe('armed');
    expect(store.getState().escArmedAt).toBe(1000);
    expect(store.getState().blurTimerActive).toBe(true);
    store.getState().reset();
  });

  it('a second ESC within the 0.45s window commits (dismiss-without-submit) and disarms', () => {
    const store = createNoteCaptureStore();
    store.getState().pressEscape(1000);
    // 449ms later — inside the 450ms window.
    const outcome = store.getState().pressEscape(1000 + ESC_DOUBLE_TAP_MS - 1);
    expect(outcome).toBe('commit');
    expect(store.getState().escArmedAt).toBeNull();
    expect(store.getState().blurTimerActive).toBe(false);
    store.getState().reset();
  });

  it('a second ESC at or past the window re-arms instead of committing', () => {
    const store = createNoteCaptureStore();
    store.getState().pressEscape(1000);
    // Exactly 450ms later — the window is `< ESC_DOUBLE_TAP_MS`, so this is NOT a double-tap.
    const outcome = store.getState().pressEscape(1000 + ESC_DOUBLE_TAP_MS);
    expect(outcome).toBe('armed');
    expect(store.getState().escArmedAt).toBe(1000 + ESC_DOUBLE_TAP_MS);
    store.getState().reset();
  });

  it('plain draft edits never arm ESC', () => {
    const store = createNoteCaptureStore();
    store.getState().setDraft('hello');
    expect(store.getState().escArmedAt).toBeNull();
    expect(store.getState().blurTimerActive).toBe(false);
    store.getState().reset();
  });
});

describe('noteCaptureStore — ESC-then-d delete chord', () => {
  it('snapshots the draft, clears it, and cancels the blur/arming', () => {
    const store = createNoteCaptureStore();
    store.getState().setDraft('a plan to capture');
    // ESC arms (this is what gates `d` into the delete chord at the dispatch layer).
    store.getState().pressEscape(2000);
    expect(store.getState().blurTimerActive).toBe(true);

    const snapshot = store.getState().pressDelete();
    expect(snapshot).toBe('a plan to capture');
    expect(store.getState().draftText).toBe('');
    expect(store.getState().undoSnapshot).toBe('a plan to capture');
    // The chord consumes the arming + blur timer.
    expect(store.getState().escArmedAt).toBeNull();
    expect(store.getState().blurTimerActive).toBe(false);
    store.getState().reset();
  });

  it('the cancelled blur never fires after a delete (no late focus move)', async () => {
    const store = createNoteCaptureStore();
    store.getState().setDraft('draft body');
    store.getState().pressEscape(3000);
    store.getState().pressDelete();
    // Wait past the blur delay — focus must stay on the draft (the delete cancelled the timer).
    await wait(BLUR_DELAY_MS + 40);
    expect(store.getState().focus).toBe('draft');
    store.getState().reset();
  });
});

describe('noteCaptureStore — blur timeout', () => {
  it('after the 0.35s idle delay the draft blurs to the list and disarms', async () => {
    const store = createNoteCaptureStore();
    store.getState().setDraft('keep me');
    store.getState().pressEscape(4000);
    expect(store.getState().focus).toBe('draft');
    expect(store.getState().blurTimerActive).toBe(true);

    await wait(BLUR_DELAY_MS + 40);

    expect(store.getState().focus).toBe('list');
    // The blur resets arming so a further ESC dismisses via the list path, not a phantom double-tap.
    expect(store.getState().escArmedAt).toBeNull();
    expect(store.getState().blurTimerActive).toBe(false);
    // The draft text is untouched by a blur (only focus moves).
    expect(store.getState().draftText).toBe('keep me');
    store.getState().reset();
  });

  it('reset cancels a pending blur so it never fires late', async () => {
    const store = createNoteCaptureStore();
    store.getState().pressEscape(5000);
    store.getState().reset();
    await wait(BLUR_DELAY_MS + 40);
    // reset put focus back to draft; the cancelled timer must not move it to the list afterwards.
    expect(store.getState().focus).toBe('draft');
    store.getState().reset();
  });
});

describe('noteCaptureStore — undo', () => {
  it('restores the draft cleared by the last delete, then consumes the snapshot', () => {
    const store = createNoteCaptureStore();
    store.getState().setDraft('important note');
    store.getState().pressEscape(6000);
    store.getState().pressDelete();
    expect(store.getState().draftText).toBe('');

    const undone = store.getState().pressUndo();
    expect(undone).toBe(true);
    expect(store.getState().draftText).toBe('important note');
    // Single-level: the snapshot is consumed, so a second undo is a no-op.
    expect(store.getState().undoSnapshot).toBeNull();
    expect(store.getState().pressUndo()).toBe(false);
    store.getState().reset();
  });

  it('undo with nothing deleted is a no-op (so an unmatched `u` falls through to entry)', () => {
    const store = createNoteCaptureStore();
    store.getState().setDraft('untouched');
    expect(store.getState().pressUndo()).toBe(false);
    expect(store.getState().draftText).toBe('untouched');
    store.getState().reset();
  });
});
