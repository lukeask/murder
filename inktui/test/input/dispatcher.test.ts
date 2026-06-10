/**
 * dispatcher tests — the layered dispatch decision, pure over synthesised key events. Covers the
 * three layers, the global chords (alt/meta-modified: the dispatcher routes on `key.meta + input`,
 * chosen over ctrl because terminals can't byte-encode Ctrl+digit but DO send Alt+<key> as an
 * ESC-prefixed `meta` event), and the rule that a panel's declared key fires only when focused.
 */

import { describe, expect, it, vi } from 'vitest';
import { resolveBindings } from '../../src/input/bindings.js';
import {
  type DispatchContext,
  dispatchKey,
  type GlobalHandlers,
} from '../../src/input/dispatcher.js';
import { CHAT_FOCUS, type FocusId } from '../../src/input/focusStore.js';
import type { PanelKeymap } from '../../src/input/keymap.js';
import type { Mode } from '../../src/input/modeStore.js';
import { makeKey } from './key.js';

/** The spy-handler bundle: each global handler is a `vi.fn` so call sites assert on it. Typed per
 * handler with its real signature so the spies are structurally the `GlobalHandlers` the dispatcher
 * wants — no cast needed. */
interface SpyHandlers {
  readonly focusPanel: ReturnType<typeof vi.fn<GlobalHandlers['focusPanel']>>;
  readonly navigate: ReturnType<typeof vi.fn<GlobalHandlers['navigate']>>;
  readonly focusChat: ReturnType<typeof vi.fn<GlobalHandlers['focusChat']>>;
  readonly spawn: ReturnType<typeof vi.fn<GlobalHandlers['spawn']>>;
  readonly toggleTmux: ReturnType<typeof vi.fn<GlobalHandlers['toggleTmux']>>;
  readonly newPlan: ReturnType<typeof vi.fn<GlobalHandlers['newPlan']>>;
  readonly newTicket: ReturnType<typeof vi.fn<GlobalHandlers['newTicket']>>;
  readonly openSettings: ReturnType<typeof vi.fn<GlobalHandlers['openSettings']>>;
}

function handlers(): SpyHandlers {
  return {
    focusPanel: vi.fn<GlobalHandlers['focusPanel']>(),
    navigate: vi.fn<GlobalHandlers['navigate']>(),
    focusChat: vi.fn<GlobalHandlers['focusChat']>(),
    spawn: vi.fn<GlobalHandlers['spawn']>(),
    toggleTmux: vi.fn<GlobalHandlers['toggleTmux']>(),
    newPlan: vi.fn<GlobalHandlers['newPlan']>(),
    newTicket: vi.fn<GlobalHandlers['newTicket']>(),
    openSettings: vi.fn<GlobalHandlers['openSettings']>(),
  };
}

function ctx(
  focusedId: FocusId,
  h: SpyHandlers,
  panelKeymaps: DispatchContext['panelKeymaps'] = {},
  activeMode: DispatchContext['activeMode'] = null,
  bindings?: DispatchContext['bindings'],
): DispatchContext {
  // Omitting `bindings` exercises the DEFAULT_BINDINGS fallback (today's alt behavior) — the
  // zero-behavior-change guarantee for existing call sites.
  return { focusedId, handlers: h, panelKeymaps, activeMode, ...(bindings ? { bindings } : {}) };
}

describe('layer 0 — active-mode capture', () => {
  const onModeIntent = vi.fn();
  /** A mode that declares `y`→confirm and Esc→dismiss; capture-everything (no pass-through). */
  function captureMode(passThrough = false): Mode {
    return {
      id: 'm',
      presentation: 'modal',
      passThrough,
      keymap: [
        { chord: { input: 'y' }, intent: 'confirm', description: 'yes' },
        { chord: { key: { escape: true } }, intent: 'dismiss', description: 'cancel' },
      ],
      onIntent: onModeIntent,
      render: () => null,
    };
  }

  it('routes a matching key to the active mode (handled), nothing below fires', () => {
    onModeIntent.mockClear();
    const h = handlers();
    const out = dispatchKey('y', makeKey(), ctx('tickets', h, {}, captureMode()));
    expect(onModeIntent).toHaveBeenCalledWith('confirm');
    expect(out).toEqual({ layer: 'mode', handled: true });
    expect(h.focusPanel).not.toHaveBeenCalled();
  });

  it('matches a special-key chord (Esc) — dismiss is just a declared chord', () => {
    onModeIntent.mockClear();
    const out = dispatchKey(
      '',
      makeKey({ escape: true }),
      ctx('chat', handlers(), {}, captureMode()),
    );
    expect(onModeIntent).toHaveBeenCalledWith('dismiss');
    expect(out).toEqual({ layer: 'mode', handled: true });
  });

  it('SWALLOWS an unmatched key (no pass-through) — global chords suppressed under the modal', () => {
    onModeIntent.mockClear();
    const h = handlers();
    // alt+1 would normally focus a panel; under a capturing modal it must NOT.
    const out = dispatchKey('1', makeKey({ meta: true }), ctx('chat', h, {}, captureMode()));
    expect(h.focusPanel).not.toHaveBeenCalled();
    expect(onModeIntent).not.toHaveBeenCalled();
    expect(out).toEqual({ layer: 'mode', handled: false });
  });

  it('falls through to lower layers when the mode declares pass-through', () => {
    onModeIntent.mockClear();
    const h = handlers();
    // alt+1 unmatched by the mode, but pass-through is on → layer 1 fires.
    const out = dispatchKey('1', makeKey({ meta: true }), ctx('chat', h, {}, captureMode(true)));
    expect(h.focusPanel).toHaveBeenCalledWith('plans');
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('calls onUncaptured for unmatched keys when present — returns handled:true if consumed', () => {
    onModeIntent.mockClear();
    const consumed: string[] = [];
    const modeWithUncaptured: Mode = {
      ...captureMode(),
      onUncaptured(input) {
        if (input.length > 0 && input !== '\x01') {
          consumed.push(input);
          return true;
        }
        return false;
      },
    };
    const h = handlers();
    // 'a' is not in the mode keymap, but onUncaptured returns true → mode consumed it.
    const out = dispatchKey('a', makeKey(), ctx('tickets', h, {}, modeWithUncaptured));
    expect(consumed).toEqual(['a']);
    expect(out).toEqual({ layer: 'mode', handled: true });
    expect(h.focusPanel).not.toHaveBeenCalled();
  });

  it('onUncaptured returning false still swallows when passThrough is off', () => {
    onModeIntent.mockClear();
    const modeWithUncaptured: Mode = {
      ...captureMode(),
      onUncaptured(_input) {
        return false; // not consumed
      },
    };
    const h = handlers();
    // alt+1 unmatched; onUncaptured returns false; no pass-through → swallow (handled:false).
    const out = dispatchKey('1', makeKey({ meta: true }), ctx('chat', h, {}, modeWithUncaptured));
    expect(h.focusPanel).not.toHaveBeenCalled(); // global chord suppressed by modal
    expect(out).toEqual({ layer: 'mode', handled: false });
  });
});

describe('layer 1 — global chords', () => {
  it('alt+<n> focuses the mapped panel (1 → plans), even while chat is focused', () => {
    const h = handlers();
    const out = dispatchKey('1', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.focusPanel).toHaveBeenCalledWith('plans');
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+0 maps to crows (right region, screen-position mapping)', () => {
    const h = handlers();
    dispatchKey('0', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.focusPanel).toHaveBeenCalledWith('crows');
  });

  it('a reserved digit (alt+5) is a no-op', () => {
    const h = handlers();
    const out = dispatchKey('5', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.focusPanel).not.toHaveBeenCalled();
    expect(out.layer).toBe('chat'); // falls through to the chat short-circuit
  });

  it('alt+h/j/k/l navigate', () => {
    const h = handlers();
    dispatchKey('h', makeKey({ meta: true }), ctx('plans', h));
    dispatchKey('j', makeKey({ meta: true }), ctx('plans', h));
    dispatchKey('k', makeKey({ meta: true }), ctx('plans', h));
    dispatchKey('l', makeKey({ meta: true }), ctx('plans', h));
    expect(h.navigate.mock.calls.map((c) => c[0])).toEqual(['left', 'down', 'up', 'right']);
  });

  it('alt+space focuses chat, alt+y toggles tmux', () => {
    const h = handlers();
    dispatchKey(' ', makeKey({ meta: true }), ctx('plans', h));
    dispatchKey('y', makeKey({ meta: true }), ctx('plans', h));
    expect(h.focusChat).toHaveBeenCalledOnce();
    expect(h.toggleTmux).toHaveBeenCalledOnce();
  });

  it('alt+f does NOT claim at the global layer — it falls through to the focused panel keymap', () => {
    const h = handlers();
    const onIntent = vi.fn();
    // A panel that declares alt+f → star (the generalized favorite/star pattern).
    const starKeymap: PanelKeymap = {
      keymap: [
        { chord: { input: 'f', key: { meta: true } }, intent: 'star', description: 'favorite' },
      ],
      onIntent,
    };
    const out = dispatchKey('f', makeKey({ meta: true }), ctx('plans', h, { plans: starKeymap }));
    expect(h.focusChat).not.toHaveBeenCalled(); // global layer declined alt+f
    expect(onIntent).toHaveBeenCalledWith('star'); // layer 3 (panel keymap) handled it
    expect(out).toEqual({ layer: 'panel', handled: true });
  });

  it('alt+s spawns ONLY when chat is focused (C11 dual-purpose chord)', () => {
    const h = handlers();
    const out = dispatchKey('s', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.spawn).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+s does NOT spawn when a panel is focused — it falls through (panels no longer use alt+s)', () => {
    const h = handlers();
    // Panels no longer declare an alt+s binding; the global layer declines and it falls through to
    // layer 3, where the focused panel declares nothing for it → unhandled.
    const out = dispatchKey('s', makeKey({ meta: true }), ctx('plans', h, {}));
    expect(h.spawn).not.toHaveBeenCalled(); // global layer declined alt+s (not chat-focused)
    expect(out).toEqual({ layer: 'panel', handled: false });
  });

  it('alt+p fires newPlan (C12 new-plan chord)', () => {
    const h = handlers();
    const out = dispatchKey('p', makeKey({ meta: true }), ctx('plans', h));
    expect(h.newPlan).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+t fires newTicket (C12 new-ticket chord)', () => {
    const h = handlers();
    const out = dispatchKey('t', makeKey({ meta: true }), ctx('plans', h));
    expect(h.newTicket).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+o fires openSettings (Phase 5 settings chord)', () => {
    const h = handlers();
    const out = dispatchKey('o', makeKey({ meta: true }), ctx('plans', h));
    expect(h.openSettings).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('alt+o fires openSettings even while chat is focused (app-wide chord)', () => {
    const h = handlers();
    const out = dispatchKey('o', makeKey({ meta: true }), ctx(CHAT_FOCUS, h));
    expect(h.openSettings).toHaveBeenCalledOnce();
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('a plain (non-alt) char is not a global chord', () => {
    const h = handlers();
    const out = dispatchKey('1', makeKey({ meta: false }), ctx(CHAT_FOCUS, h));
    expect(h.focusPanel).not.toHaveBeenCalled();
    expect(out.layer).toBe('chat');
  });
});

describe('layer 2 — chat short-circuit', () => {
  it('yields a non-chord event to the input when chat is focused (handled=false)', () => {
    const h = handlers();
    const out = dispatchKey('x', makeKey(), ctx(CHAT_FOCUS, h));
    expect(out).toEqual({ layer: 'chat', handled: false });
  });
});

describe('layer 3 — focused panel keymap', () => {
  const onIntent = vi.fn();
  const plansKeymap: PanelKeymap = {
    keymap: [{ chord: { input: 'a' }, intent: 'act', description: 'act' }],
    onIntent,
  };

  it('fires the panel intent when that panel is focused', () => {
    onIntent.mockClear();
    const out = dispatchKey('a', makeKey(), ctx('plans', handlers(), { plans: plansKeymap }));
    expect(onIntent).toHaveBeenCalledWith('act');
    expect(out).toEqual({ layer: 'panel', handled: true });
  });

  it('does NOT fire the panel intent when a different panel is focused', () => {
    onIntent.mockClear();
    // 'a' is plans' key, but tickets is focused (and declares nothing) → no intent.
    const out = dispatchKey('a', makeKey(), ctx('tickets', handlers(), { plans: plansKeymap }));
    expect(onIntent).not.toHaveBeenCalled();
    expect(out).toEqual({ layer: 'panel', handled: false });
  });

  it('ignores a key the focused panel did not declare', () => {
    onIntent.mockClear();
    const out = dispatchKey('z', makeKey(), ctx('plans', handlers(), { plans: plansKeymap }));
    expect(onIntent).not.toHaveBeenCalled();
    expect(out).toEqual({ layer: 'panel', handled: false });
  });
});

describe('command modifier — ctrl', () => {
  const ctrlBindings = resolveBindings('ctrl', true, {});

  it('ctrl+<n> focuses the mapped panel (digit gate honours ctrl)', () => {
    const h = handlers();
    const out = dispatchKey(
      '1',
      makeKey({ ctrl: true }),
      ctx(CHAT_FOCUS, h, {}, null, ctrlBindings),
    );
    expect(h.focusPanel).toHaveBeenCalledWith('plans');
    expect(out).toEqual({ layer: 'global', handled: true });
  });

  it('ctrl+y toggles tmux, ctrl+space focuses chat', () => {
    const h = handlers();
    dispatchKey('y', makeKey({ ctrl: true }), ctx('plans', h, {}, null, ctrlBindings));
    dispatchKey(' ', makeKey({ ctrl: true }), ctx('plans', h, {}, null, ctrlBindings));
    expect(h.toggleTmux).toHaveBeenCalledOnce();
    expect(h.focusChat).toHaveBeenCalledOnce();
  });

  it('ctrl+s spawns only when chat is focused', () => {
    const h = handlers();
    const chatOut = dispatchKey(
      's',
      makeKey({ ctrl: true }),
      ctx(CHAT_FOCUS, h, {}, null, ctrlBindings),
    );
    expect(h.spawn).toHaveBeenCalledOnce();
    expect(chatOut).toEqual({ layer: 'global', handled: true });
    const panelOut = dispatchKey(
      's',
      makeKey({ ctrl: true }),
      ctx('plans', h, {}, null, ctrlBindings),
    );
    expect(panelOut).toEqual({ layer: 'panel', handled: false });
  });

  it('alt+<n> is NOT a command chord under ctrl-only (degraded to chat short-circuit)', () => {
    const h = handlers();
    const out = dispatchKey(
      '1',
      makeKey({ meta: true }),
      ctx(CHAT_FOCUS, h, {}, null, ctrlBindings),
    );
    expect(h.focusPanel).not.toHaveBeenCalled();
    expect(out.layer).toBe('chat');
  });

  it('a panel-resolved ctrl+f stars (panel keymap built from the same bindings)', () => {
    const h = handlers();
    const onIntent = vi.fn();
    const starKeymap: PanelKeymap = {
      keymap: [{ chord: ctrlBindings.chordsFor('panel.star'), intent: 'star', description: 'fav' }],
      onIntent,
    };
    const out = dispatchKey(
      'f',
      makeKey({ ctrl: true }),
      ctx('plans', h, { plans: starKeymap }, null, ctrlBindings),
    );
    expect(onIntent).toHaveBeenCalledWith('star');
    expect(out).toEqual({ layer: 'panel', handled: true });
  });
});

describe('command modifier — both (alt still works)', () => {
  const bothBindings = resolveBindings('both', true, {});

  it('alt+<n> AND ctrl+<n> both focus the panel', () => {
    const h = handlers();
    dispatchKey('1', makeKey({ meta: true }), ctx(CHAT_FOCUS, h, {}, null, bothBindings));
    dispatchKey('2', makeKey({ ctrl: true }), ctx(CHAT_FOCUS, h, {}, null, bothBindings));
    expect(h.focusPanel.mock.calls.map((c) => c[0])).toEqual(['plans', 'notes']);
  });

  it('alt+y and ctrl+y both toggle tmux', () => {
    const h = handlers();
    dispatchKey('y', makeKey({ meta: true }), ctx('plans', h, {}, null, bothBindings));
    dispatchKey('y', makeKey({ ctrl: true }), ctx('plans', h, {}, null, bothBindings));
    expect(h.toggleTmux).toHaveBeenCalledTimes(2);
  });
});
