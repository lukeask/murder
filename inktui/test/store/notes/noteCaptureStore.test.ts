/**
 * `noteCaptureStore` tests — the F9 port of Textual's `NoteCaptureScreen` capture FSM
 * (`app/tui/note_capture.py:109-247`). One case per FSM transition the plan names:
 *  - ESC cancel,
 *  - the delete chord,
 *  - the single-level undo of a delete.
 *
 * Style mirrors `toastStore.test.ts`: a fresh factory instance per case.
 */

import { describe, expect, it } from 'vitest';
import { createNoteCaptureStore } from '../../../src/store/notes/noteCaptureStore.js';

describe('noteCaptureStore — ESC cancel', () => {
  it('ESC commits immediately and does not arm a second-press window', () => {
    const store = createNoteCaptureStore();
    store.getState().setDraft('keep this draft');
    const outcome = store.getState().pressEscape();
    expect(outcome).toBe('commit');
    expect(store.getState().draftText).toBe('keep this draft');
    store.getState().reset();
  });

  it('plain draft edits only change the draft text', () => {
    const store = createNoteCaptureStore();
    store.getState().setDraft('hello');
    expect(store.getState().draftText).toBe('hello');
    store.getState().reset();
  });
});

describe('noteCaptureStore — delete snapshot', () => {
  it('snapshots the draft and clears it', () => {
    const store = createNoteCaptureStore();
    store.getState().setDraft('a plan to capture');

    const snapshot = store.getState().pressDelete();
    expect(snapshot).toBe('a plan to capture');
    expect(store.getState().draftText).toBe('');
    expect(store.getState().undoSnapshot).toBe('a plan to capture');
    store.getState().reset();
  });
});

describe('noteCaptureStore — undo', () => {
  it('restores the draft cleared by the last delete, then consumes the snapshot', () => {
    const store = createNoteCaptureStore();
    store.getState().setDraft('important note');
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
