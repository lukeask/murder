/**
 * The shared global-chord focus gate. These lock the table that both the dispatcher and the bottom
 * bar read, so the two can never drift again (the bug: live chords missing from the bar).
 */

import { describe, expect, it } from 'vitest';
import { ACTIONS } from '../../src/input/bindings.js';
import { CHAT_FOCUS } from '../../src/input/focusStore.js';
import { GLOBAL_ACTION_IDS, GLOBAL_SCOPE, inFocusScope } from '../../src/input/globalScope.js';

describe('inFocusScope', () => {
  it('always: live from every focus', () => {
    for (const f of [CHAT_FOCUS, 'plans', 'crows', 'stage:doc:x'] as const) {
      expect(inFocusScope('always', f)).toBe(true);
    }
  });

  it('not-chat: every focus except the chat input', () => {
    expect(inFocusScope('not-chat', CHAT_FOCUS)).toBe(false);
    expect(inFocusScope('not-chat', 'plans')).toBe(true);
    expect(inFocusScope('not-chat', 'stage:transcript:a1')).toBe(true);
  });

  it('chat: only the chat input', () => {
    expect(inFocusScope('chat', CHAT_FOCUS)).toBe(true);
    expect(inFocusScope('chat', 'plans')).toBe(false);
  });

  it('chat-or-stage: chat input OR a Stage pane, not a list panel', () => {
    expect(inFocusScope('chat-or-stage', CHAT_FOCUS)).toBe(true);
    expect(inFocusScope('chat-or-stage', 'stage:doc:readme')).toBe(true);
    expect(inFocusScope('chat-or-stage', 'plans')).toBe(false);
  });

  it('stage: only a Stage pane', () => {
    expect(inFocusScope('stage', 'stage:transcript:a1')).toBe(true);
    expect(inFocusScope('stage', CHAT_FOCUS)).toBe(false);
    expect(inFocusScope('stage', 'plans')).toBe(false);
  });

  it('not-crows: every focus except the crows panel', () => {
    expect(inFocusScope('not-crows', 'crows')).toBe(false);
    expect(inFocusScope('not-crows', 'plans')).toBe(true);
    expect(inFocusScope('not-crows', CHAT_FOCUS)).toBe(true);
  });
});

describe('GLOBAL_SCOPE', () => {
  it('every scoped id is a real action with a description (so the bar can label it)', () => {
    for (const id of GLOBAL_ACTION_IDS) {
      expect(ACTIONS[id]).toBeDefined();
      expect(ACTIONS[id].description.length).toBeGreaterThan(0);
    }
  });

  it('matches the dispatcher gating that motivated the table', () => {
    expect(GLOBAL_SCOPE['global.closePane']).toBe('stage');
    expect(GLOBAL_SCOPE['global.spawn']).toBe('chat-or-stage');
    expect(GLOBAL_SCOPE['global.murder']).toBe('not-crows');
    expect(GLOBAL_SCOPE['global.keyHelp']).toBe('not-chat');
    expect(GLOBAL_SCOPE['global.cycleTargetPrev']).toBe('chat');
  });
});
